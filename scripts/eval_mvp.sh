#!/usr/bin/env bash

set -x

CFG_FILE="configs/mvp/shelf_config/mvp_shelf.py"
# CFG_FILE="configs/mvp/campus_config/mvp_campus.py"
# CFG_FILE="configs/mvp/panoptic_config/mvp_panoptic.py"
# CFG_FILE="configs/mvp/panoptic_config/mvp_panoptic_3cam.py"

# Author provided/ trained with original github code
# MODEL_PATH="weight/convert_mvp_shelf.pth.tar" # 97.2
# MODEL_PATH="weight/convert_mvp_shelf_self.pth.tar" # 97.1
# MODEL_PATH="weight/convert_mvp_campus.pth.tar" #90.8
# MODEL_PATH="weight/convert_model_best_5view.pth.tar" # ap25:92.3

# Trained with xrmocap from scratch
MODEL_PATH="weight/convert_mvp_shelf_self_wo_band3_8gpu.pth.tar" #97.1
# MODEL_PATH="weight/convert_mvp_campus_self_wo_band3_31223_2gpu.pth.tar" #96.79
# MODEL_PATH="weight/convert_model_best_5view_self_wo_band3.pth.tar" #ap25:90.7
# MODEL_PATH="weight/convert_model_best_31223.pth.tar" #ap25:53.54


GPUS_PER_NODE=$1


python -m torch.distributed.launch \
    --nproc_per_node=${GPUS_PER_NODE} \
    --master_port 65524 \
    --use_env tool/val_model.py \
    --cfg ${CFG_FILE} \
    --model_path ${MODEL_PATH} | tee ./output/log.txt