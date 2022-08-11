# yapf: disable

import numpy as np
import os
import random
import time
import torch
import torch.distributed as dist
import torch.optim as optim
import torch.utils.data
import torch.utils.data.distributed
import torchvision.transforms as transforms
from mmcv.runner import get_dist_info, load_checkpoint
from prettytable import PrettyTable
from torch.utils.data import DistributedSampler
from xrprimer.utils.log_utils import get_logger

from xrmocap.data_structure.keypoints import Keypoints
from xrmocap.dataset.campus import Campus as campus  # noqa F401
from xrmocap.dataset.panoptic import Panoptic as panoptic  # noqa F401
from xrmocap.dataset.shelf import Shelf as shelf  # noqa F401
from xrmocap.model.architecture.builder import build_architecture
from xrmocap.utils.distribute_utils import (
    collect_results, get_rank, is_main_process, time_synchronized,
)
from xrmocap.utils.mvp_utils import (
    AverageMeter, convert_result_to_kps, get_total_grad_norm,
    match_name_keywords, norm2absolute, save_checkpoint, set_cudnn,
)

# yapf: enable


class MVPTrainer():

    def __init__(self, logger, device, seed, distributed, model_path, gpu_idx,
                 workers, train_dataset, test_dataset, train_batch_size,
                 test_batch_size, lr, lr_linear_proj_mult, weight_decay,
                 optimizer, end_epoch, pretrained_backbone, model_root,
                 finetune_model, resume, normalize, final_output_dir,
                 lr_decay_epoch, inference_conf_thr, test_model_file,
                 clip_max_norm, print_freq, cudnn_setup, dataset_setup,
                 mvp_setup):

        self.logger = get_logger(logger)
        self.device = device
        self.seed = seed
        self.distributed = distributed
        self.model_path = model_path
        self.gpu_dix = gpu_idx

        self.train_dataset = train_dataset
        self.test_dataset = test_dataset
        self.train_batch_size = train_batch_size
        self.test_batch_size = test_batch_size
        self.lr = lr
        self.lr_linear_proj_mult = lr_linear_proj_mult
        self.weight_decay = weight_decay
        self.optimizer = optimizer
        self.end_epoch = end_epoch
        self.pretrained_backbone = pretrained_backbone
        self.model_root = model_root
        self.finetune_model = finetune_model
        self.resume = resume
        self.lr_decay_epoch = lr_decay_epoch
        self.inference_conf_thr = inference_conf_thr
        self.test_model_file = test_model_file
        self.clip_max_norm = clip_max_norm
        self.print_freq = print_freq
        self.workers = workers
        self.final_output_dir = final_output_dir
        self.normalize = normalize

        self.cudnn_setup = cudnn_setup
        self.dataset_setup = dataset_setup
        self.mvp_setup = mvp_setup

        seed = self.seed + get_rank()
        torch.manual_seed(seed)
        np.random.seed(seed)
        random.seed(seed)

    def get_optimizer(self, model_without_ddp, weight_decay, optim_type):
        lr = self.lr
        if model_without_ddp.backbone is not None:
            for params in model_without_ddp.backbone.parameters():
                # Set it to be True to train the whole model jointly
                params.requires_grad = True  # dufault false

        lr_linear_proj_mult = self.lr_linear_proj_mult
        lr_linear_proj_names = ['reference_points', 'sampling_offsets']
        param_dicts = [{
            'params': [
                p for n, p in model_without_ddp.named_parameters()
                if not match_name_keywords(n, lr_linear_proj_names)
                and p.requires_grad
            ],
            'lr':
            lr,
        }, {
            'params': [
                p for n, p in model_without_ddp.named_parameters()
                if match_name_keywords(n, lr_linear_proj_names)
                and p.requires_grad
            ],
            'lr':
            lr * lr_linear_proj_mult,
        }]

        if optim_type == 'adam':
            optimizer = optim.Adam(param_dicts, lr=lr)
        elif optim_type == 'adamw':
            optimizer = optim.AdamW(param_dicts, lr=lr, weight_decay=1e-4)

        return optimizer

    def train(self):

        if is_main_process():
            self.logger.info('Loading data ..')

        normalize = transforms.Normalize(**self.normalize)

        train_dataset = eval(self.train_dataset)(
            is_train=True,
            image_set=self.dataset_setup.train_subset,
            n_kps=self.dataset_setup.n_kps,
            n_max_person=self.dataset_setup.n_max_person,
            dataset_root=self.dataset_setup.root,
            root_idx=self.dataset_setup.root_idx,
            dataset=self.dataset_setup.dataset,
            data_format=self.dataset_setup.data_format,
            data_augmentation=self.dataset_setup.data_augmentation,
            n_cameras=self.dataset_setup.n_cameras,
            scale_factor=self.dataset_setup.scale_factor,
            rot_factor=self.dataset_setup.rot_factor,
            flip=self.dataset_setup.flip,
            color_rgb=self.dataset_setup.color_rgb,
            target_type=self.dataset_setup.target_type,
            heatmap_size=self.dataset_setup.heatmap_size,
            image_size=self.dataset_setup.image_size,
            use_different_kps_weight=self.dataset_setup.
            use_different_kps_weight,
            space_size=self.dataset_setup.space_size,
            space_center=self.dataset_setup.space_center,
            initial_cube_size=self.dataset_setup.initial_cube_size,
            pesudo_gt=self.dataset_setup.pesudo_gt,
            sigma=self.dataset_setup.sigma,
            transform=transforms.Compose([
                transforms.ToTensor(),
                normalize,
            ]))

        test_dataset = eval(self.test_dataset)(
            is_train=False,
            image_set=self.dataset_setup.test_subset,
            n_kps=self.dataset_setup.n_kps,
            n_max_person=self.dataset_setup.n_max_person,
            dataset_root=self.dataset_setup.root,
            root_idx=self.dataset_setup.root_idx,
            dataset=self.dataset_setup.dataset,
            data_format=self.dataset_setup.data_format,
            data_augmentation=self.dataset_setup.data_augmentation,
            n_cameras=self.dataset_setup.n_cameras,
            scale_factor=self.dataset_setup.scale_factor,
            rot_factor=self.dataset_setup.rot_factor,
            flip=self.dataset_setup.flip,
            color_rgb=self.dataset_setup.color_rgb,
            target_type=self.dataset_setup.target_type,
            heatmap_size=self.dataset_setup.heatmap_size,
            image_size=self.dataset_setup.image_size,
            use_different_kps_weight=self.dataset_setup.
            use_different_kps_weight,
            space_size=self.dataset_setup.space_size,
            space_center=self.dataset_setup.space_center,
            initial_cube_size=self.dataset_setup.initial_cube_size,
            pesudo_gt=self.dataset_setup.pesudo_gt,
            sigma=self.dataset_setup.sigma,
            transform=transforms.Compose([
                transforms.ToTensor(),
                normalize,
            ]))

        n_views = train_dataset.n_views

        if self.distributed:
            rank, world_size = get_dist_info()
            sampler_train = DistributedSampler(train_dataset)
            sampler_val = DistributedSampler(
                test_dataset, world_size, rank, shuffle=False)
        else:
            sampler_train = torch.utils.data.RandomSampler(train_dataset)
            sampler_val = torch.utils.data.SequentialSampler(test_dataset)

        batch_sampler_train = torch.utils.data.BatchSampler(
            sampler_train, self.train_batch_size, drop_last=True)

        train_loader = torch.utils.data.DataLoader(
            train_dataset,
            batch_sampler=batch_sampler_train,
            num_workers=self.workers,
            pin_memory=True)

        test_loader = torch.utils.data.DataLoader(
            test_dataset,
            batch_size=self.test_batch_size,
            sampler=sampler_val,
            pin_memory=True,
            num_workers=self.workers)

        set_cudnn(self.cudnn_setup.benchmark, self.cudnn_setup.deterministic,
                  self.cudnn_setup.enable)

        if is_main_process():
            self.logger.info('Constructing models ..')

        mvp_cfg = dict(
            type='MviewPoseTransformer', is_train=True, logger=self.logger)
        mvp_cfg.update(self.mvp_setup)
        model = build_architecture(mvp_cfg)

        model.to(self.device)
        model.criterion.to(self.device)

        model_without_ddp = model
        if self.distributed:
            if is_main_process():
                self.logger.info('Distributed ..')
            model = torch.nn.parallel.\
                DistributedDataParallel(model, device_ids=[self.gpu_dix],
                                        find_unused_parameters=True)
            model_without_ddp = model.module

        optimizer = self.get_optimizer(model_without_ddp, self.weight_decay,
                                       self.optimizer)

        end_epoch = self.end_epoch

        if self.pretrained_backbone:
            # Load pretrained poseresnet weight for panoptic only
            checkpoint_file = os.path.join(self.model_root,
                                           self.pretrained_backbone)
            load_checkpoint(
                model_without_ddp.backbone,
                checkpoint_file,
                map_location='cpu',
                logger=self.logger)
        if self.finetune_model is not None:
            # Load the checkpoint with state_dict only
            checkpoint_file = os.path.join(self.model_root,
                                           self.finetune_model)
            checkpoint = load_checkpoint(
                model_without_ddp,
                checkpoint_file,
                map_location='cpu',
                logger=self.logger)
            start_epoch = 0
            best_precision = checkpoint['precision'] \
                if 'precision' in checkpoint else 0
        if self.resume:
            # Load the checkpoint with full dict keys
            checkpoint_file = os.path.join(self.final_output_dir,
                                           'checkpoint.pth.tar')
            checkpoint = load_checkpoint(
                model_without_ddp,
                checkpoint_file,
                map_location='cpu',
                logger=self.logger)
            start_epoch = checkpoint['epoch']
            best_precision = checkpoint['precision'] \
                if 'precision' in checkpoint else 0
            optimizer.load_state_dict(checkpoint['optimizer'])

        else:
            start_epoch, checkpoint, best_precision = 0, None, 0

        # list for step decay
        if isinstance(self.lr_decay_epoch, list):
            lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(
                optimizer, milestones=self.lr_decay_epoch, gamma=0.1)
            if checkpoint is not None and 'lr_scheduler' in checkpoint:
                lr_scheduler.load_state_dict(checkpoint['lr_scheduler'])
        # int for cosine decay
        else:
            lr_scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer, self.lr_decay_epoch, eta_min=1e-5)
            if checkpoint is not None and 'lr_scheduler' in checkpoint:
                lr_scheduler.load_state_dict(checkpoint['lr_scheduler'])

        n_parameters = sum(p.numel() for p in model.parameters()
                           if p.requires_grad)
        if is_main_process():
            self.logger.info(f'Number of params: {n_parameters}')

        for epoch in range(start_epoch, end_epoch):
            current_lr = optimizer.param_groups[0]['lr']
            if is_main_process():
                self.logger.info(f'Epoch: {epoch}, current lr: {current_lr}')
            train_3d(
                model,
                optimizer,
                train_loader,
                epoch,
                self.logger,
                self.final_output_dir,
                self.clip_max_norm,
                self.print_freq,
                n_views=n_views)

            lr_scheduler.step()

            inference_conf_thr = self.inference_conf_thr
            for thr in inference_conf_thr:
                preds_single, meta_image_files_single, _ = validate_3d(
                    model,
                    test_loader,
                    self.logger,
                    self.final_output_dir,
                    thr,
                    self.print_freq,
                    n_views=n_views,
                    is_train=True)
                preds = collect_results(preds_single, len(test_dataset))

                if is_main_process():

                    precision = None

                    if 'panoptic' in self.test_dataset:
                        tb = PrettyTable()
                        mpjpe_threshold = np.arange(25, 155, 25)
                        aps, recs, mpjpe, recall500 = \
                            test_loader.dataset.evaluate(preds)
                        tb.field_names = ['Threshold/mm'] + \
                                         [f'{i}' for i in mpjpe_threshold]
                        tb.add_row(['AP'] + [f'{ap * 100:.2f}' for ap in aps])
                        tb.add_row(['Recall'] +
                                   [f'{re * 100:.2f}' for re in recs])
                        tb.add_row(['recall@500mm'] +
                                   [f'{recall500 * 100:.2f}' for re in recs])
                        self.logger.info(tb)
                        self.logger.info(f'MPJPE: {mpjpe:.2f}mm')
                        precision = np.mean(aps[0])

                    elif 'campus' in self.test_dataset \
                            or 'shelf' in self.test_dataset:
                        actor_pcp, avg_pcp, _, recall = \
                            test_loader.dataset.evaluate(preds)

                        tb = PrettyTable()
                        tb.field_names = [
                            'Metric', 'Actor 1', 'Actor 2', 'Actor 3',
                            'Average'
                        ]
                        tb.add_row([
                            'PCP', f'{actor_pcp[0] * 100:.2f}',
                            f'{actor_pcp[1] * 100:.2f}',
                            f'{actor_pcp[2] * 100:.2f}', f'{avg_pcp * 100:.2f}'
                        ])
                        self.logger.info('\n' + tb.get_string())
                        self.logger.info(f'Recall@500mm: {recall:.4f}')

                        precision = np.mean(avg_pcp)

                    if precision > best_precision:
                        best_precision = precision
                        best_model = True
                    else:
                        best_model = False
                    if isinstance(self.lr_decay_epoch, list):
                        self.logger.info(
                            f'saving checkpoint to {self.final_output_dir} '
                            f'(Best: {best_model})')
                        save_checkpoint(
                            {
                                'epoch': epoch + 1,
                                'state_dict': model.module.state_dict(),
                                'lr_scheduler': lr_scheduler.state_dict(),
                                'precision': best_precision,
                                'optimizer': optimizer.state_dict(),
                            }, best_model, self.final_output_dir)
                    else:
                        self.logger.info(
                            f'saving checkpoint to {self.final_output_dir} '
                            f'(Best: {best_model})')
                        save_checkpoint(
                            {
                                'epoch': epoch + 1,
                                'state_dict': model.module.state_dict(),
                                'precision': best_precision,
                                'optimizer': optimizer.state_dict(),
                            }, best_model, self.final_output_dir)
                dist.barrier()

        if is_main_process():
            final_model_state_file = os.path.join(self.final_output_dir,
                                                  'final_state.pth.tar')
            self.logger.info(
                f'saving final model state to {final_model_state_file}')
            torch.save(model.module.state_dict(), final_model_state_file)

    def eval(self):

        if is_main_process():
            self.logger.info('Loading data ..')

        normalize = transforms.Normalize(**self.normalize)

        test_dataset = eval(self.test_dataset)(
            is_train=False,
            image_set=self.dataset_setup.test_subset,
            n_kps=self.dataset_setup.n_kps,
            n_max_person=self.dataset_setup.n_max_person,
            dataset_root=self.dataset_setup.root,
            root_idx=self.dataset_setup.root_idx,
            dataset=self.dataset_setup.dataset,
            data_format=self.dataset_setup.data_format,
            data_augmentation=self.dataset_setup.data_augmentation,
            n_cameras=self.dataset_setup.n_cameras,
            scale_factor=self.dataset_setup.scale_factor,
            rot_factor=self.dataset_setup.rot_factor,
            flip=self.dataset_setup.flip,
            color_rgb=self.dataset_setup.color_rgb,
            target_type=self.dataset_setup.target_type,
            heatmap_size=self.dataset_setup.heatmap_size,
            image_size=self.dataset_setup.image_size,
            use_different_kps_weight=self.dataset_setup.
            use_different_kps_weight,
            space_size=self.dataset_setup.space_size,
            space_center=self.dataset_setup.space_center,
            initial_cube_size=self.dataset_setup.initial_cube_size,
            pesudo_gt=self.dataset_setup.pesudo_gt,
            sigma=self.dataset_setup.sigma,
            transform=transforms.Compose([
                transforms.ToTensor(),
                normalize,
            ]))

        if self.distributed:
            rank, world_size = get_dist_info()
            sampler_val = DistributedSampler(
                test_dataset, world_size, rank, shuffle=False)
        else:
            sampler_val = torch.utils.data.SequentialSampler(test_dataset)

        test_loader = torch.utils.data.DataLoader(
            test_dataset,
            batch_size=self.test_batch_size,
            sampler=sampler_val,
            pin_memory=True,
            num_workers=self.workers)

        n_views = test_dataset.n_views

        set_cudnn(self.cudnn_setup.benchmark, self.cudnn_setup.deterministic,
                  self.cudnn_setup.enable)

        if is_main_process():
            self.logger.info('Constructing models ..')

        mvp_cfg = dict(
            type='MviewPoseTransformer', is_train=False, logger=self.logger)
        mvp_cfg.update(self.mvp_setup)
        model = build_architecture(mvp_cfg)

        model.to(self.device)
        model.criterion.to(self.device)

        if self.distributed:
            model = torch.nn.parallel.DistributedDataParallel(
                model, device_ids=[self.gpu_dix], find_unused_parameters=True)

        if self.model_path is not None:
            if is_main_process():
                self.logger.info(f'Load saved models state {self.model_path}')

            load_checkpoint(
                model.module,
                self.model_path,
                logger=self.logger,
                map_location='cpu')

        elif os.path.isfile(
                os.path.join(self.final_output_dir, self.test_model_file)):
            test_model_file = \
                os.path.join(self.final_output_dir,
                             self.test_model_file)
            if is_main_process():
                self.logger.info(
                    f'Load default best models state {test_model_file}')
            model.module.load_state_dict(torch.load(test_model_file))
        else:
            raise ValueError('Check the model file for testing!')

        for thr in self.inference_conf_thr:
            preds_single, meta_image_files_single, kps3d = validate_3d(
                model,
                test_loader,
                self.logger,
                self.final_output_dir,
                thr,
                self.print_freq,
                n_views=n_views)
            preds = collect_results(preds_single, len(test_dataset))

            if is_main_process():
                if 'panoptic' in self.test_dataset:
                    tb = PrettyTable()
                    mpjpe_threshold = np.arange(25, 155, 25)
                    aps, recs, mpjpe, recall500 = \
                        test_loader.dataset.evaluate(preds)
                    tb.field_names = ['Threshold/mm'] + \
                                     [f'{i}' for i in mpjpe_threshold]
                    tb.add_row(['AP'] + [f'{ap * 100:.2f}' for ap in aps])
                    tb.add_row(['Recall'] + [f'{re * 100:.2f}' for re in recs])
                    tb.add_row(['recall@500mm'] +
                               [f'{recall500 * 100:.2f}' for re in recs])
                    self.logger.info(tb)
                    self.logger.info(f'MPJPE: {mpjpe:.2f}mm')

                elif 'campus' in self.test_dataset \
                        or 'shelf' in self.test_dataset:
                    actor_pcp, avg_pcp, _, recall = \
                        test_loader.dataset.evaluate(preds)

                    tb = PrettyTable()
                    tb.field_names = [
                        'Metric', 'Actor 1', 'Actor 2', 'Actor 3', 'Average'
                    ]
                    tb.add_row([
                        'PCP', f'{actor_pcp[0] * 100:.2f}',
                        f'{actor_pcp[1] * 100:.2f}',
                        f'{actor_pcp[2] * 100:.2f}', f'{avg_pcp * 100:.2f}'
                    ])
                    self.logger.info('\n' + tb.get_string())
                    self.logger.info(f'Recall@500mm: {recall:.4f}')


def train_3d(model,
             optimizer,
             loader,
             epoch,
             logger,
             output_dir,
             clip_max_norm,
             print_freq,
             device=torch.device('cuda'),
             n_views=5):
    logger = get_logger(logger)
    batch_time = AverageMeter()
    data_time = AverageMeter()
    loss_ce = AverageMeter()
    class_error = AverageMeter()
    loss_per_kp = AverageMeter()
    loss_pose_perprojection = AverageMeter()
    cardinality_error = AverageMeter()

    model.train()

    if model.module.backbone is not None:
        # Comment out this line if you want to train 2D backbone jointly
        model.module.backbone.eval()

    end = time.time()
    for i, (inputs, meta) in enumerate(loader):
        assert len(inputs) == n_views
        inputs = [i.to(device) for i in inputs]
        meta = [{
            k: v.to(device) if isinstance(v, torch.Tensor) else v
            for k, v in t.items()
        } for t in meta]
        data_time.update(time_synchronized() - end)
        end = time_synchronized()

        out, loss_dict, losses = model(views=inputs, meta=meta)

        gt_kps3d = meta[0]['kps3d'].float()
        n_kps = gt_kps3d.shape[2]
        bs, n_queries = out['pred_logits'].shape[:2]

        src_poses = out['pred_poses']['outputs_coord']. \
            view(bs, n_queries, n_kps, 3)
        src_poses = norm2absolute(src_poses, model.module.grid_size,
                                  model.module.grid_center)
        score = out['pred_logits'][:, :, 1:2].sigmoid()
        score = score.unsqueeze(2).expand(-1, -1, n_kps, -1)

        loss_ce.update(loss_dict['loss_ce'].sum().item())
        class_error.update(loss_dict['class_error'].sum().item())

        loss_per_kp.update(loss_dict['loss_per_kp'].sum().item())

        if 'loss_pose_perprojection' in loss_dict:
            loss_pose_perprojection.update(
                loss_dict['loss_pose_perprojection'].sum().item())

        cardinality_error.update(loss_dict['cardinality_error'].sum().item())

        if losses > 0:
            optimizer.zero_grad()
            losses.backward()
            if clip_max_norm > 0:
                grad_total_norm = torch.nn.utils.clip_grad_norm_(
                    model.parameters(), clip_max_norm)
            else:
                grad_total_norm = get_total_grad_norm(model.parameters(),
                                                      clip_max_norm)

            optimizer.step()

        batch_time.update(time_synchronized() - end)
        end = time_synchronized()

        if i % print_freq == 0 and is_main_process():
            gpu_memory_usage = torch.cuda.memory_allocated(0)
            speed = len(inputs) * inputs[0].size(0) / batch_time.val

            msg = \
                f'Epoch: [{epoch}][{i}/{len(loader)}]\t' \
                f'Time: {batch_time.val:.3f}s ({batch_time.avg:.3f}s)\t' \
                f'Speed: {speed:.1f} samples/s\t' \
                f'Data: {data_time.val:.3f}s ' f'({data_time.avg:.3f}s)\t' \
                f'loss_ce: {loss_ce.val:.7f} ' f'({loss_ce.avg:.7f})\t' \
                f'class_error: {class_error.val:.7f} ' \
                f'({class_error.avg:.7f})\t' \
                f'loss_per_kp: {loss_per_kp.val:.6f} ' \
                f'({loss_per_kp.avg:.6f})\t' \
                f'loss_pose_perprojection: ' \
                f'{loss_pose_perprojection.val:.6f} ' \
                f'({loss_pose_perprojection.avg:.6f})\t' \
                f'cardinality_error: {cardinality_error.val:.6f} ' \
                f'({cardinality_error.avg:.6f})\t' \
                f'Memory {gpu_memory_usage:.1f}\t' \
                f'gradnorm {grad_total_norm:.2f}'

            logger.info(msg)


def validate_3d(model,
                loader,
                logger,
                output_dir,
                threshold,
                print_freq,
                n_views=5,
                is_train=False):
    batch_time = AverageMeter()
    data_time = AverageMeter()
    logger = get_logger(logger)

    model.eval()

    preds = []
    meta_image_files = []
    keypoints3d = None
    with torch.no_grad():
        end = time.time()
        kps3d_pred = []
        n_max_person = 0
        for i, (inputs, meta) in enumerate(loader):
            data_time.update(time.time() - end)
            assert len(inputs) == n_views
            output = model(views=inputs, meta=meta)

            meta_image_files.append(meta[0]['image'])
            gt_kps3d = meta[0]['kps3d'].float()
            n_kps = gt_kps3d.shape[2]
            bs, n_queries = output['pred_logits'].shape[:2]

            src_poses = output['pred_poses']['outputs_coord']. \
                view(bs, n_queries, n_kps, 3)
            src_poses = norm2absolute(src_poses, model.module.grid_size,
                                      model.module.grid_center)
            score = output['pred_logits'][:, :, 1:2].sigmoid()
            score = score.unsqueeze(2).expand(-1, -1, n_kps, -1)
            temp = (score > threshold).float() - 1

            pred = torch.cat([src_poses, temp, score], dim=-1)
            pred = pred.detach().cpu().numpy()
            for b in range(pred.shape[0]):
                preds.append(pred[b])

            batch_time.update(time.time() - end)
            end = time.time()
            if (i % print_freq == 0 or i == len(loader) - 1) \
                    and is_main_process():
                gpu_memory_usage = torch.cuda.memory_allocated(0)
                speed = len(inputs) * inputs[0].size(0) / batch_time.val
                msg = f'Test: [{i}/{len(loader)}]\t' \
                      f'Time: {batch_time.val:.3f}s ' \
                      f'({batch_time.avg:.3f}s)\t' \
                      f'Speed: {speed:.1f} samples/s\t' \
                      f'Data: {data_time.val:.3f}s ({data_time.avg:.3f}s)\t' \
                      f'Memory {gpu_memory_usage:.1f}'
                logger.info(msg)

            if not is_train:
                n_person, per_frame_kps3d = convert_result_to_kps(pred)
                n_max_person = max(n_person, n_max_person)
                kps3d_pred.append(per_frame_kps3d)

        if not is_train:
            n_frame = len(kps3d_pred)
            n_kps = n_kps
            kps3d = np.full((n_frame, n_max_person, n_kps, 4), np.nan)

            for frame_idx in range(n_frame):
                per_frame_kps3d = kps3d_pred[frame_idx]
                n_person = len(per_frame_kps3d)
                if n_person > 0:
                    kps3d[frame_idx, :n_person] = per_frame_kps3d

            keypoints3d = Keypoints(kps=kps3d, convention=None)
            kps3d_file = os.path.join(output_dir, 'kps3d.npz')
            if is_main_process():
                logger.info(f'Saving 3D keypoints to: {kps3d_file}')
            keypoints3d.dump(kps3d_file)

    return preds, meta_image_files, keypoints3d