#!/usr/bin/env python3
# Copyright (c) Megvii, Inc. and its affiliates.
# SPDX-License-Identifier: Apache-2.0
"""Dangjia two-class experiment adapted from YOLOX's official nano config."""

import os

from torch import nn
from yolox.exp import Exp as YoloXExp


class Exp(YoloXExp):
    def __init__(self):
        super().__init__()
        self.num_classes = 2
        self.depth = 0.33
        self.width = 0.25
        self.input_size = (416, 416)
        self.test_size = (416, 416)
        self.random_size = (10, 20)
        self.mosaic_scale = (0.5, 1.5)
        self.mosaic_prob = 0.5
        self.enable_mixup = False
        self.max_epoch = 150
        self.no_aug_epochs = 15
        self.eval_interval = 5
        self.data_num_workers = int(os.environ.get("DANGJIA_DATA_WORKERS", "4"))
        self.data_dir = os.environ["DANGJIA_DATASET_DIR"]
        self.train_ann = "instances_train.json"
        self.val_ann = "instances_val.json"
        self.output_dir = os.environ.get("DANGJIA_OUTPUT_DIR", "YOLOX_outputs")
        self.exp_name = "dangjia_logo_nano_v1"

    def get_model(self, sublinear=False):
        del sublinear

        def init_yolo(module):
            for layer in module.modules():
                if isinstance(layer, nn.BatchNorm2d):
                    layer.eps = 1e-3
                    layer.momentum = 0.03

        if "model" not in self.__dict__:
            from yolox.models import YOLOPAFPN, YOLOX, YOLOXHead

            in_channels = [256, 512, 1024]
            backbone = YOLOPAFPN(
                self.depth,
                self.width,
                in_channels=in_channels,
                act=self.act,
                depthwise=True,
            )
            head = YOLOXHead(
                self.num_classes,
                self.width,
                in_channels=in_channels,
                act=self.act,
                depthwise=True,
            )
            self.model = YOLOX(backbone, head)

        self.model.apply(init_yolo)
        self.model.head.initialize_biases(1e-2)
        return self.model
