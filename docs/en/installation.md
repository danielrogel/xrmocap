# Installation

<!-- TOC -->

- [Installation](#installation)
  - [Requirements](#requirements)
  - [Prepare environment](#prepare-environment)
  - [A from-scratch setup script](#a-from-scratch-setup-script)
  - [Run with docker image](#run-with-docker-image)

<!-- TOC -->

## Requirements

- Linux
- ffmpeg
- Python 3.7+
- PyTorch 1.6.0, 1.7.0, 1.7.1, 1.8.0, 1.8.1, 1.9.0 or 1.9.1.
- CUDA 9.2+
- GCC 5+
- [XRPrimer](https://gitlab.bj.sensetime.com/openxrlab/xrprimer)
- [MMHuman3D](https://github.com/open-mmlab/mmhuman3d)
- [MMCV](https://github.com/open-mmlab/mmcv)

Optional:

| Name                                                     | When it is required       | What's important                                             |
| :------------------------------------------------------- | :------------------------ | :----------------------------------------------------------- |
| [MMPose](https://github.com/open-mmlab/mmpose)           | Keypoints 2D estimation.  | Install `mmcv-full`, instead of `mmcv`.                      |
| [MMDetection](https://github.com/open-mmlab/mmdetection) | Bbox 2D estimation.       | Install `mmcv-full`, instead of `mmcv`.                      |
| [MMTracking](https://github.com/open-mmlab/mmtracking)   | Multiple object tracking. | Install `mmcv-full`, instead of `mmcv`.                      |
| [Aniposelib](https://github.com/google/aistplusplus_api) | Triangulation.            | Install from [github](https://github.com/liruilong940607/aniposelib), instead of pypi. |

## Prepare environment

##### a. Create a conda virtual environment and activate it.

```shell
conda create -n xrmocap python=3.8 -y
conda activate xrmocap
```

##### b. Install MMHuman3D following the [official instructions](https://github.com/open-mmlab/mmhuman3d/blob/main/docs/install.md).

Important: Make sure that your compilation CUDA version and runtime CUDA version match.

##### c. Install XRPrimer following the [official instructions](https://gitlab.bj.sensetime.com/openxrlab/xrprimer/-/blob/xrprimer_ee_dev/docs/python/install.md).

##### d. Install XRMoCap to virtual environment,  in editable mode.

```shell
pip install -r requirements/build.txt
pip install -r requirements/runtime.txt
pip install -e .
```

## A from-scratch setup script

```shell
conda create -n xrmocap python=3.8
source activate xrmocap

# install ffmpeg for video and images
conda install -y ffmpeg

# install pytorch
conda install -y pytorch==1.8.1 torchvision==0.9.1 cudatoolkit=10.1 -c pytorch

# install pytorch3d
conda install -y -c fvcore -c iopath -c conda-forge fvcore iopath
conda install -y -c bottler nvidiacub
conda install -y pytorch3d -c pytorch3d

# install mmcv-full
pip install mmcv-full==1.5.3 -f https://download.openmmlab.com/mmcv/dist/cu101/torch1.8.1/index.html

# install xrprimer
pip install xrprimer -i https://repo.sensetime.com/repository/pypi/simple

# install requirements for build
pip install -r requirements/build.txt
# install requirements for runtime
pip install -r requirements/runtime.txt

# install xrmocap
rm -rf .eggs && pip install -e .
```

### Run with Docker Image

We provide a [Dockerfile](../../Dockerfile) to build an image. Ensure that you are using [docker version](https://docs.docker.com/engine/install/) >=19.03 and `"default-runtime": "nvidia"` in daemon.json.

```shell
# build an image with PyTorch 1.8.1, CUDA 10.2
docker build -t xrmocap .
```

Run it with

```shell
docker run --gpus all --shm-size=8g -it -v {DATA_DIR}:/xrmocap/data xrmocap
```