#!/usr/bin/env python
# -*- coding: utf-8 -*-
import numpy as np
import tensorflow as tf
import os


class KeyPointClassifier(object):
    def __init__(
        self,
        model_path="model/keypoint_classifier/keypoint_classifier.tflite",
        num_threads=1,
    ):
        self.interpreter = tf.lite.Interpreter(
            model_path=model_path, num_threads=num_threads
        )

        self.interpreter.allocate_tensors()
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()

    def __call__(
        self,
        landmark_list,
    ):
        input_details_tensor_index = self.input_details[0]["index"]
        self.interpreter.set_tensor(
            input_details_tensor_index, np.array([landmark_list], dtype=np.float32)
        )
        self.interpreter.invoke()

        output_details_tensor_index = self.output_details[0]["index"]

        result = self.interpreter.get_tensor(output_details_tensor_index)

        # Squeeze to remove unnecessary dimensions and find the index of the maximum score
        result_squeezed = np.squeeze(result)
        result_index = np.argmax(result_squeezed)

        # Get the confidence score for the most likely gesture
        confidence = result_squeezed[result_index]

        # Return both the gesture index and its confidence score
        return result_index, confidence

        # result_index = np.argmax(np.squeeze(result))

        # return result_index
