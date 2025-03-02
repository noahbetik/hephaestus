#!/usr/bin/env python
# -*- coding: utf-8 -*-
import csv
import copy
import argparse
import itertools
import json
from collections import Counter
from collections import deque
from datetime import datetime
import time
import sys
import cv2 as cv
import numpy as np
import mediapipe as mp

from utils import CvFpsCalc
from model import KeyPointClassifier
from model import PointHistoryClassifier
from tcp.tcp_send import TCPClient


import os

import socket

print("Current Working Directory:", os.getcwd())


def load_gesture_definitions(filename):
    with open(filename, "r") as file:
        gesture_definitions = json.load(file)
    return gesture_definitions


def get_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--width", help="cap width", type=int, default=960)
    parser.add_argument("--height", help="cap height", type=int, default=540)

    parser.add_argument("--use_static_image_mode", action="store_true")
    parser.add_argument(
        "--min_detection_confidence",
        help="min_detection_confidence",
        type=float,
        default=0.7,
    )
    parser.add_argument(
        "--min_tracking_confidence",
        help="min_tracking_confidence",
        type=int,
        default=0.5,
    )

    args = parser.parse_args()

    return args


def start_command(
    gesture_type, gesture_subtype, point_history, axis="z", snap_view="iso"
):
    match (gesture_type, gesture_subtype):
        case ("motion", "zoom"):  # 2 fingers
            return f"motion zoom start {point_history[-1][0]}"
        case ("motion", "pan"):  # 3 fingers
            return f"motion pan start ({point_history[-1][0]},{point_history[-1][1]})"
        case ("motion", "rotate"):  # fist
            return f"motion rotate start {axis} {point_history[-1][0]}"
        case ("create", "line"):  # pointer
            return f"create line start ({point_history[-2][0]},{point_history[-2][1]}) ({point_history[-1][0]},{point_history[-1][1]})"  # from noah: i do it this way to make parsing easier on UI side
        case ("toggle", "mode"):
            return "toggle mode"
        case ("toggle", "motion"):
            return "toggle motion"
        case ("snap", "None"):
            return f"snap {snap_view}"
        case (
            "deselect",
            _,
        ):  # Assuming any deselect action follows a generic pattern
            return "deselect {params}"
        case _:
            return "Command not found"


def active_command(gesture_type, gesture_subtype, point_history):
    match (gesture_type, gesture_subtype):
        case ("motion", "zoom"):
            return f"motion zoom position {point_history[-1][0]}"
        case ("motion", "pan"):
            return (
                f"motion pan position ({point_history[-1][0]},{point_history[-1][1]})"
            )
        case ("motion", "rotate"):
            return f"motion rotate position {point_history[-1][0]}"
        case ("create", "line"):
            return f"create line ({point_history[-1][0]},{point_history[-1][1]})"
        case ("toggle", "mode"):
            return "toggle mode"
        case ("toggle", "motion"):
            return ""
        case ("snap", "None"):
            return ""
        case ("snap_righthand", "None"):
            return ""
        case ("snap_lefthand", "None"):
            return ""
        case ("snap_iso", "left"):
            return ""
        case ("snap_iso", "right"):
            return ""
        case (
            "deselect",
            _,
        ):  # Assuming any deselect action follows a generic pattern
            return "deselect {params}"
        case _:
            return "Command not found"


def select_mode(key, mode):
    number = -1
    if 48 <= key <= 57:  # 0 ~ 9
        number = key - 48
    elif key == 113:  # using q for 10
        number = 10
    elif key == 119:  # using w for 11
        number = 11
    elif key == 101:  # using e for 12 
        number = 12
    elif key == 114:  # using r for 13 
        number = 13
    if key == 110:  # n
        mode = 0
    if key == 107:  # k
        mode = 1
    if key == 104:  # h
        mode = 2
    return number, mode


def calc_bounding_rect(image, landmarks):
    image_width, image_height = image.shape[1], image.shape[0]

    landmark_array = np.empty((0, 2), int)

    for _, landmark in enumerate(landmarks.landmark):
        landmark_x = min(int(landmark.x * image_width), image_width - 1)
        landmark_y = min(int(landmark.y * image_height), image_height - 1)

        landmark_point = [np.array((landmark_x, landmark_y))]

        landmark_array = np.append(landmark_array, landmark_point, axis=0)

    x, y, w, h = cv.boundingRect(landmark_array)

    return [x, y, x + w, y + h]


def calc_landmark_list(image, landmarks):
    image_width, image_height = image.shape[1], image.shape[0]

    landmark_point = []

    # Keypoint
    for _, landmark in enumerate(landmarks.landmark):
        landmark_x = min(int(landmark.x * image_width), image_width - 1)
        landmark_y = min(int(landmark.y * image_height), image_height - 1)
        # landmark_z = landmark.z

        landmark_point.append([landmark_x, landmark_y])

    return landmark_point


def pre_process_landmark(landmark_list):
    temp_landmark_list = copy.deepcopy(landmark_list)

    # Convert to relative coordinates
    base_x, base_y = 0, 0
    for index, landmark_point in enumerate(temp_landmark_list):
        if index == 0:
            base_x, base_y = landmark_point[0], landmark_point[1]

        temp_landmark_list[index][0] = temp_landmark_list[index][0] - base_x
        temp_landmark_list[index][1] = temp_landmark_list[index][1] - base_y

    # Convert to a one-dimensional list
    temp_landmark_list = list(itertools.chain.from_iterable(temp_landmark_list))

    # Normalization
    max_value = max(list(map(abs, temp_landmark_list)))

    def normalize_(n):
        return n / max_value

    temp_landmark_list = list(map(normalize_, temp_landmark_list))

    return temp_landmark_list


def pre_process_point_history(image, point_history):
    image_width, image_height = image.shape[1], image.shape[0]

    temp_point_history = copy.deepcopy(point_history)

    # Convert to relative coordinates
    base_x, base_y = 0, 0
    for index, point in enumerate(temp_point_history):
        if index == 0:
            base_x, base_y = point[0], point[1]

        temp_point_history[index][0] = (
            temp_point_history[index][0] - base_x
        ) / image_width
        temp_point_history[index][1] = (
            temp_point_history[index][1] - base_y
        ) / image_height

    # Convert to a one-dimensional list
    temp_point_history = list(itertools.chain.from_iterable(temp_point_history))

    return temp_point_history


def logging_csv(number, mode, landmark_list, point_history_list):
    if mode == 0:
        pass
    if mode == 1 and (0 <= number <= 13):
        csv_path = "model/keypoint_classifier/keypoint.csv"
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([number, *landmark_list])
    if mode == 2 and (0 <= number <= 9):
        csv_path = "model/point_history_classifier/point_history.csv"
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([number, *point_history_list])
    return


def draw_landmarks(image, landmark_point):
    if len(landmark_point) > 0:
        # Thumb
        cv.line(image, tuple(landmark_point[2]), tuple(landmark_point[3]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[2]),
            tuple(landmark_point[3]),
            (255, 255, 255),
            2,
        )
        cv.line(image, tuple(landmark_point[3]), tuple(landmark_point[4]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[3]),
            tuple(landmark_point[4]),
            (255, 255, 255),
            2,
        )

        # Index finger
        cv.line(image, tuple(landmark_point[5]), tuple(landmark_point[6]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[5]),
            tuple(landmark_point[6]),
            (255, 255, 255),
            2,
        )
        cv.line(image, tuple(landmark_point[6]), tuple(landmark_point[7]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[6]),
            tuple(landmark_point[7]),
            (255, 255, 255),
            2,
        )
        cv.line(image, tuple(landmark_point[7]), tuple(landmark_point[8]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[7]),
            tuple(landmark_point[8]),
            (255, 255, 255),
            2,
        )

        # Middle finger
        cv.line(
            image, tuple(landmark_point[9]), tuple(landmark_point[10]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[9]),
            tuple(landmark_point[10]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[10]), tuple(landmark_point[11]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[10]),
            tuple(landmark_point[11]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[11]), tuple(landmark_point[12]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[11]),
            tuple(landmark_point[12]),
            (255, 255, 255),
            2,
        )

        # Ring finger
        cv.line(
            image, tuple(landmark_point[13]), tuple(landmark_point[14]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[13]),
            tuple(landmark_point[14]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[14]), tuple(landmark_point[15]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[14]),
            tuple(landmark_point[15]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[15]), tuple(landmark_point[16]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[15]),
            tuple(landmark_point[16]),
            (255, 255, 255),
            2,
        )

        # Little finger
        cv.line(
            image, tuple(landmark_point[17]), tuple(landmark_point[18]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[17]),
            tuple(landmark_point[18]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[18]), tuple(landmark_point[19]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[18]),
            tuple(landmark_point[19]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[19]), tuple(landmark_point[20]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[19]),
            tuple(landmark_point[20]),
            (255, 255, 255),
            2,
        )

        # Palm
        cv.line(image, tuple(landmark_point[0]), tuple(landmark_point[1]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[0]),
            tuple(landmark_point[1]),
            (255, 255, 255),
            2,
        )
        cv.line(image, tuple(landmark_point[1]), tuple(landmark_point[2]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[1]),
            tuple(landmark_point[2]),
            (255, 255, 255),
            2,
        )
        cv.line(image, tuple(landmark_point[2]), tuple(landmark_point[5]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[2]),
            tuple(landmark_point[5]),
            (255, 255, 255),
            2,
        )
        cv.line(image, tuple(landmark_point[5]), tuple(landmark_point[9]), (0, 0, 0), 6)
        cv.line(
            image,
            tuple(landmark_point[5]),
            tuple(landmark_point[9]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[9]), tuple(landmark_point[13]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[9]),
            tuple(landmark_point[13]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[13]), tuple(landmark_point[17]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[13]),
            tuple(landmark_point[17]),
            (255, 255, 255),
            2,
        )
        cv.line(
            image, tuple(landmark_point[17]), tuple(landmark_point[0]), (0, 0, 0), 6
        )
        cv.line(
            image,
            tuple(landmark_point[17]),
            tuple(landmark_point[0]),
            (255, 255, 255),
            2,
        )

    # Key Points
    for index, landmark in enumerate(landmark_point):
        if index == 0:  # 手首1
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 1:  # 手首2
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 2:  # 親指：付け根
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 3:  # 親指：第1関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 4:  # 親指：指先
            cv.circle(image, (landmark[0], landmark[1]), 8, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 8, (0, 0, 0), 1)
        if index == 5:  # 人差指：付け根
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 6:  # 人差指：第2関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 7:  # 人差指：第1関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 8:  # 人差指：指先
            cv.circle(image, (landmark[0], landmark[1]), 8, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 8, (0, 0, 0), 1)
        if index == 9:  # 中指：付け根
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 10:  # 中指：第2関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 11:  # 中指：第1関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 12:  # 中指：指先
            cv.circle(image, (landmark[0], landmark[1]), 8, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 8, (0, 0, 0), 1)
        if index == 13:  # 薬指：付け根
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 14:  # 薬指：第2関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 15:  # 薬指：第1関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 16:  # 薬指：指先
            cv.circle(image, (landmark[0], landmark[1]), 8, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 8, (0, 0, 0), 1)
        if index == 17:  # 小指：付け根
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 18:  # 小指：第2関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 19:  # 小指：第1関節
            cv.circle(image, (landmark[0], landmark[1]), 5, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 5, (0, 0, 0), 1)
        if index == 20:  # 小指：指先
            cv.circle(image, (landmark[0], landmark[1]), 8, (255, 255, 255), -1)
            cv.circle(image, (landmark[0], landmark[1]), 8, (0, 0, 0), 1)

    return image


def draw_bounding_rect(use_brect, image, brect):
    if use_brect:
        # Outer rectangle
        cv.rectangle(image, (brect[0], brect[1]), (brect[2], brect[3]), (0, 0, 0), 1)

    return image


def draw_info_text(image, brect, handedness, hand_sign_text, finger_gesture_text):
    cv.rectangle(image, (brect[0], brect[1]), (brect[2], brect[1] - 22), (0, 0, 0), -1)

    info_text = handedness.classification[0].label[0:]
    if hand_sign_text != "":
        info_text = info_text + ":" + hand_sign_text
    cv.putText(
        image,
        info_text,
        (brect[0] + 5, brect[1] - 4),
        cv.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
        cv.LINE_AA,
    )

    if finger_gesture_text != "":
        cv.putText(
            image,
            "Finger Gesture:" + finger_gesture_text,
            (10, 60),
            cv.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 0),
            4,
            cv.LINE_AA,
        )
        cv.putText(
            image,
            "Finger Gesture:" + finger_gesture_text,
            (10, 60),
            cv.FONT_HERSHEY_SIMPLEX,
            1.0,
            (255, 255, 255),
            2,
            cv.LINE_AA,
        )

    return image


def draw_point_history(image, point_history):
    # for index, point in enumerate(point_history):
    #     if point[0] != 0 and point[1] != 0:
    #         cv.circle(
    #             image, (point[0], point[1]), 1 + int(index / 2), (152, 251, 152), 2
    #         )

    # Initialize an empty string to accumulate coordinates
    coordinates_text = ""

    for index, point in enumerate(point_history):
        if point[0] != 0 and point[1] != 0:
            # Draw circle for each point in history
            cv.circle(
                image, (point[0], point[1]), 1 + int(index / 2), (152, 251, 152), 2
            )
            # Append the current point's coordinates to the string
            coordinates_text += f"({point[0]}, {point[1]}) "

    # Display all coordinates in the top right corner of the image
    # Calculate the position based on the image size and the length of the coordinates_text
    text_size = cv.getTextSize(coordinates_text, cv.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
    text_x = image.shape[1] - text_size[0] - 10  # 10 pixels from the right edge
    text_y = 20  # 20 pixels from the top

    # Draw a background rectangle for better readability
    cv.rectangle(
        image, (text_x, text_y - 14), (text_x + text_size[0], text_y + 5), (0, 0, 0), -1
    )

    # Display the coordinates text
    cv.putText(
        image,
        coordinates_text,
        (text_x, text_y),
        cv.FONT_HERSHEY_SIMPLEX,
        0.5,  # Font scale
        (255, 255, 255),  # Font color
        1,  # Thickness
        cv.LINE_AA,
    )
    return image


def draw_current_pointer_coordinates(image, point_history):
    if not point_history:
        return image  # If the history is empty, return the image as is

    # Assuming the last point in point_history is the current pointer
    current_point = point_history[-1]

    if current_point[0] == 0 and current_point[1] == 0:
        return (
            image  # If the current point is (0,0), it's considered invalid/not present
        )

    # Prepare the text to display the current point's coordinates
    coordinates_text = f"Current: ({current_point[0]}, {current_point[1]})"

    # Determine the position for the text (top right corner)
    text_size = cv.getTextSize(coordinates_text, cv.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
    text_x = image.shape[1] - text_size[0] - 10  # 10 pixels from the right edge
    text_y = 20  # 20 pixels from the top

    # Draw a background rectangle for better readability
    cv.rectangle(
        image,
        (text_x - 5, text_y - 14),
        (text_x + text_size[0] + 5, text_y + 5),
        (0, 0, 0),
        -1,
    )

    # Display the coordinates text
    cv.putText(
        image,
        coordinates_text,
        (text_x, text_y),
        cv.FONT_HERSHEY_SIMPLEX,
        0.5,  # Font scale
        (255, 255, 255),  # Font color
        1,  # Thickness
        cv.LINE_AA,
    )

    return image


def draw_info(image, fps, mode, number):
    cv.putText(
        image,
        "FPS:" + str(fps),
        (10, 30),
        cv.FONT_HERSHEY_SIMPLEX,
        1.0,
        (0, 0, 0),
        4,
        cv.LINE_AA,
    )
    cv.putText(
        image,
        "FPS:" + str(fps),
        (10, 30),
        cv.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
        cv.LINE_AA,
    )

    mode_string = ["Logging Key Point", "Logging Point History"]
    if 1 <= mode <= 2:
        cv.putText(
            image,
            "MODE:" + mode_string[mode - 1],
            (10, 90),
            cv.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            1,
            cv.LINE_AA,
        )
        if 0 <= number <= 13:
            cv.putText(
                image,
                "NUM:" + str(number),
                (10, 110),
                cv.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv.LINE_AA,
            )
    return image


def argument_parsing():
    # Argument parsing #################################################################
    args = get_args()

    cap_device = args.device
    cap_width = args.width
    cap_height = args.height

    use_static_image_mode = args.use_static_image_mode
    min_detection_confidence = args.min_detection_confidence
    min_tracking_confidence = args.min_tracking_confidence

    use_brect = True

    # Camera preparation ###############################################################
    cap = cv.VideoCapture(cap_device)
    # Replace with the same PORT used in the ffmpeg command
    # stream_url = 'udp://@:444'

    # cap = cv.VideoCapture(stream_url)
    cap.set(cv.CAP_PROP_FRAME_WIDTH, cap_width)
    cap.set(cv.CAP_PROP_FRAME_HEIGHT, cap_height)

    return (
        cap,
        use_static_image_mode,
        min_detection_confidence,
        min_tracking_confidence,
        use_brect,
    )


def create_tcp_client():
    return TCPClient(host="localhost", port=4445)  # Adjust host and port if needed


def process_key(cv):
    key = cv.waitKey(10)
    if key == 27:  # ESC
        return None
    return key


def capture_and_preprocess_frame(cap, cv):
    ret, image = cap.read()
    if not ret:
        return None, None
    image = cv.flip(image, 1)  # Mirror display
    debug_image = copy.deepcopy(image)
    return image, debug_image


def process_detection(image, hands, cv, point_history, keypoint_classifier):
    image_rgb = cv.cvtColor(image, cv.COLOR_BGR2RGB)
    image_rgb.flags.writeable = False  # set to read only
    results = hands.process(image_rgb)
    image_rgb.flags.writeable = True
    return results


def main():

    # Parse arguments provided in python command
    (
        cap,
        use_static_image_mode,
        min_detection_confidence,
        min_tracking_confidence,
        use_brect,
    ) = argument_parsing()

    # Initialize and connect the TCP client
    tcp_client = create_tcp_client()
    tcp_client.connect()

    # Model load #############################################################
    mp_hands = mp.solutions.hands  # initialize mediapipe's hand solution
    hands = mp_hands.Hands(
        static_image_mode=use_static_image_mode,  # treats each image as static, better to set as True if not a video stream
        max_num_hands=2,  # set 1 or 2 hands
        min_detection_confidence=min_detection_confidence,  # minimum values, lower = easier to detect but more false positives
        min_tracking_confidence=min_tracking_confidence,
    )

    # Instantiate objects for key point and point history classifier
    keypoint_classifier = KeyPointClassifier()
    point_history_classifier = PointHistoryClassifier()

    # Read labels ###########################################################
    # Opens CSV files with labels for classification
    with open(
        "model/keypoint_classifier/keypoint_classifier_label.csv", encoding="utf-8-sig"
    ) as f:
        keypoint_classifier_labels = csv.reader(f)
        keypoint_classifier_labels = [row[0] for row in keypoint_classifier_labels]
    with open(
        "model/point_history_classifier/point_history_classifier_label.csv",
        encoding="utf-8-sig",
    ) as f:
        point_history_classifier_labels = csv.reader(f)
        point_history_classifier_labels = [
            row[0] for row in point_history_classifier_labels
        ]

    # FPS Measurement ########################################################
    cvFpsCalc = CvFpsCalc(buffer_len=10)  # use last 10 buffers to calculate fps

    # Coordinate history #################################################################
    history_length = 16  # max length
    point_history = deque(maxlen=history_length)  # double ended queue

    # Finger gesture history ################################################
    finger_gesture_history = deque(maxlen=history_length)

    #  ########################################################################
    mode = 0

    gesture_counter = 0
    gesture_lock_threshold = 20  # Number of frames to confirm gesture lock
    gesture_confidence_threshold = 0.60  # Confidence level to be valid
    locked_in = False
    previous_hand_sign_id = None
    previous_left_hand_gesture_id = None
    previous_right_hand_gesture_id = None
    active_gesture_id = None
    gesture_type = None
    gesture_subtype = None
    hand_sign_name = "No Gesture"

    left_hand_gesture_id = None
    right_hand_gesture_id = None
    dual_gesture_detected = False  # Used to detect a dual gesture
    dual_gesture = None
    axis = None
    snap_view = None

    # Get gesture types
    gesture_types = load_gesture_definitions("./tcp/gestures.json")

    while True:
        fps = cvFpsCalc.get()

        # Process Key (ESC: end)
        key = process_key(cv)
        if key == None:
            break
        number, mode = select_mode(key, mode)

        # Camera capture
        image, debug_image = capture_and_preprocess_frame(cap, cv)

        # Detection implementation
        image = cv.cvtColor(image, cv.COLOR_BGR2RGB)

        image.flags.writeable = False  # set to read only

        results = hands.process(
            image
        )  # mediapipe processing, results holds detected hand landmarks
        image.flags.writeable = True

        #  ####################################################################
        if results.multi_hand_landmarks is not None:
            for hand_landmarks, handedness in zip(
                results.multi_hand_landmarks, results.multi_handedness
            ):
                hand_label = handedness.classification[0].label  # Right or left
                # print(hand_label)
                # Bounding box calculation
                brect = calc_bounding_rect(debug_image, hand_landmarks)
                # Landmark calculation
                landmark_list = calc_landmark_list(debug_image, hand_landmarks)

                # Conversion to relative coordinates / normalized coordinates
                pre_processed_landmark_list = pre_process_landmark(landmark_list)
                pre_processed_point_history_list = pre_process_point_history(
                    debug_image, point_history
                )

                hand_sign_id, _ = keypoint_classifier(pre_processed_landmark_list)
                # _----------------------------------------------------------------------------------------------------------------------------------------
                # _----------------------------------------------------------------------------------------------------------------------------------------
                # _----------------------------------------------------------------------------------------------------------------------------------------
                # _----------------------------------------------------------------------------------------------------------------------------------------
                # _----------------------------------------------------------------------------------------------------------------------------------------
                # Assign gesture ID based on hand label
                if hand_label == "Left":
                    left_hand_gesture_id = hand_sign_id
                elif hand_label == "Right":
                    right_hand_gesture_id = hand_sign_id

                # After processing both hands, check for dual gesture
                if (
                    left_hand_gesture_id is not None
                    and right_hand_gesture_id is not None
                ):
                    # Adjust these tuples based on your gesture ID definitions
                    dual_gesture_combinations = {
                        (1, 6): "One finger and a fist",
                        (2, 6): "Two fingers and a fist",
                        (4, 6): "Three fingers and a fist",
                        (10, 11): "Illuminati",  # Snap Isometric
                        (8, 7): "L Shape",  # Snap Home
                    }

                    # Get the dual gesture combination
                    dual_gesture = dual_gesture_combinations.get(
                        (left_hand_gesture_id, right_hand_gesture_id)
                    )

                # Write to the dataset file
                logging_csv(
                    number,
                    mode,
                    pre_processed_landmark_list,
                    pre_processed_point_history_list,
                )

                # Hand sign classification
                # hand_sign_id = keypoint_classifier(pre_processed_landmark_list)
                hand_sign_id, confidence = keypoint_classifier(
                    pre_processed_landmark_list
                )

                # Dual gesture
                if dual_gesture or dual_gesture_detected:
                    if confidence >= gesture_confidence_threshold:
                        if hand_label == "Right":
                            if right_hand_gesture_id == 6:  # Fist gesture
                                point_history.append(landmark_list[8])
                        # print(f"Dual gesture detected: {dual_gesture}")
                        if not locked_in:
                            if (
                                left_hand_gesture_id == previous_left_hand_gesture_id
                                and right_hand_gesture_id
                                == previous_right_hand_gesture_id
                            ):
                                gesture_counter += 1
                                sys.stdout.write(
                                    f"\rGesture: {dual_gesture}, Frames: {gesture_counter}, Confidence: {confidence:.5f}"
                                )
                                sys.stdout.flush()
                                if gesture_counter >= gesture_lock_threshold:
                                    locked_in = True
                                    active_gesture_id = right_hand_gesture_id

                                    gesture_type = gesture_types[right_hand_sign_name][
                                        "type"
                                    ]
                                    gesture_subtype = gesture_types[
                                        right_hand_sign_name
                                    ]["subtype"]
                                    print(
                                        f"\nGesture: {dual_gesture} locked in FROM DUAL"
                                    )
                                    dual_gesture_detected = True

                                    match left_hand_gesture_id:
                                        case 1:  # 1 finger
                                            axis = "x"
                                        case 2:  # 2 fingers
                                            axis = "y"
                                        case 4:  # 3 fingers
                                            axis = "z"
                                        case 8:  # L_LeftHand, home
                                            snap_view = "home"
                                        case 10:  # Illuminati_LeftHand, iso
                                            snap_view = "iso"

                                    gesture_start_command = start_command(
                                        gesture_type,
                                        gesture_subtype,
                                        point_history,
                                        axis,
                                        snap_view,
                                    )
                                    tcp_client.send_gesture(  # Send "start" command for the gesture
                                        gesture_start_command
                                    )
                                    # Your code to handle gesture activation
                        else:
                            if right_hand_gesture_id == 5:  # 5 = thumbs down
                                if (
                                    right_hand_gesture_id
                                    == previous_right_hand_gesture_id
                                ):
                                    gesture_counter += 1

                                    sys.stdout.write(
                                        f"\rGesture: {hand_sign_name}, Frames: {gesture_counter}, Confidence: {confidence:.5f}"
                                    )
                                    sys.stdout.flush()
                                    if gesture_counter >= gesture_lock_threshold:
                                        print()
                                        tcp_client.send_gesture(
                                            f"{gesture_type} {gesture_subtype} end"
                                        )
                                        locked_in = False
                                        axis = None
                                        snap_view = None
                                        gesture_counter = 0
                                        active_gesture_id = None
                                        dual_gesture = False
                                        dual_gesture_detected = False
                                else:
                                    gesture_counter = 1
                            elif (
                                left_hand_gesture_id == 8
                                or left_hand_gesture_id == 10
                                or right_hand_gesture_id == 11
                                or right_hand_gesture_id == 7
                                and left_hand_gesture_id != 6
                            ):  # 7: L_RightHand, 11: Illuminati_RightHand
                                print(f"left_hand_gesture id = {left_hand_gesture_id}")
                                print(f"left_hand_gesture id = {right_hand_gesture_id}")
                                if (
                                    right_hand_gesture_id
                                    == previous_right_hand_gesture_id
                                ):
                                    gesture_counter += 1

                                    # sys.stdout.write(
                                    #     f"\rGesture: {hand_sign_name}, Frames: {gesture_counter}, Confidence: {confidence:.5f}"
                                    # )
                                    # sys.stdout.flush()
                                    # if gesture_counter >= gesture_lock_threshold:
                                    print("RESETTING 1")
                                    locked_in = False
                                    axis = None
                                    snap_view = None
                                    gesture_counter = 0
                                    active_gesture_id = None
                                    dual_gesture = False
                                    dual_gesture_detected = False
                                    previous_hand_sign_id = None
                                    right_hand_gesture_id = None
                                    left_hand_gesture_id = None
                                    left_hand_sign_name = None
                                    right_hand_sign_name = None
                                else:
                                    gesture_counter = 1
                            elif right_hand_gesture_id == active_gesture_id:
                                gesture_type = gesture_types[right_hand_sign_name][
                                    "type"
                                ]
                                gesture_subtype = gesture_types[right_hand_sign_name][
                                    "subtype"
                                ]
                                gesture_active_command = active_command(
                                    gesture_type, gesture_subtype, point_history
                                )
                                tcp_client.send_gesture(  # Send "active" command for the gesture
                                    gesture_active_command
                                )
                            else:
                                pass
                    # else:
                    # print(f"Confidence lost... {confidence:.5f}")
                # Single gesture
                else:
                    if not dual_gesture_detected:
                        # print("Not dual detected")
                        if confidence >= gesture_confidence_threshold:
                            if hand_sign_id == 1 or 4:  # Pointer or 3 fingers
                                point_history.append(landmark_list[8])
                            # If we're not locked in yet...
                            if not locked_in:
                                # If the current gesture detected is the same as the last frame...
                                if (
                                    hand_sign_id == previous_hand_sign_id
                                    and hand_sign_id != 5  # thumbs down
                                    and hand_sign_id != 6  # fist
                                    and left_hand_gesture_id != 8
                                    and left_hand_gesture_id != 10
                                    and right_hand_gesture_id != 11
                                    and right_hand_gesture_id != 7
                                ):
                                    gesture_counter += 1  # Increment gesture counter

                                    sys.stdout.write(
                                        f"\rGesture: {hand_sign_name}, Frames: {gesture_counter}, Confidence: {confidence:.5f}"
                                    )
                                    sys.stdout.flush()

                                    # If we reach the threshold for a gesture, lock in
                                    if gesture_counter >= gesture_lock_threshold:
                                        locked_in = True
                                        active_gesture_id = hand_sign_id
                                        gesture_type = gesture_types[hand_sign_name][
                                            "type"
                                        ]
                                        gesture_subtype = gesture_types[hand_sign_name][
                                            "subtype"
                                        ]
                                        print(f"\nGesture: {hand_sign_name} locked in")

                                        gesture_start_command = start_command(
                                            gesture_type, gesture_subtype, point_history
                                        )

                                        tcp_client.send_gesture(  # Send "start" command for the gesture
                                            gesture_start_command
                                        )

                                        if (
                                            gesture_type == "toggle"
                                            or gesture_type == "deselect"
                                            or gesture_type == "snap"
                                        ):
                                            locked_in = False
                                            gesture_counter = 0
                                            active_gesture_id = None
                                # If the current gesture is not the same as the last frame...
                                else:
                                    gesture_counter = 0  # Reset counter
                            else:
                                # If it's the thumbs down gesture...
                                if hand_sign_id == 5:
                                    if hand_sign_id == previous_hand_sign_id:
                                        gesture_counter += 1

                                        sys.stdout.write(
                                            f"\rGesture: {hand_sign_name}, Frames: {gesture_counter}, Confidence: {confidence:.5f}"
                                        )
                                        sys.stdout.flush()
                                        if gesture_counter >= gesture_lock_threshold:
                                            print()
                                            tcp_client.send_gesture(
                                                f"{gesture_type} {gesture_subtype} end"
                                            )
                                            locked_in = False
                                            gesture_counter = 0
                                            active_gesture_id = None
                                    else:
                                        gesture_counter = 1
                                elif hand_sign_id == active_gesture_id:
                                    gesture_type = gesture_types[hand_sign_name]["type"]
                                    gesture_subtype = gesture_types[hand_sign_name][
                                        "subtype"
                                    ]
                                    print("This is from DUAL SEND 3:")

                                    gesture_active_command = active_command(
                                        gesture_type, gesture_subtype, point_history
                                    )

                                    tcp_client.send_gesture(  # Send "start" command for the gesture
                                        gesture_active_command
                                    )
                                else:
                                    pass
                        else:
                            print(f"LOST CONFIDENCE {confidence:.5f}")

                previous_hand_sign_id = hand_sign_id
                if left_hand_gesture_id:
                    previous_left_hand_gesture_id = left_hand_gesture_id
                if right_hand_gesture_id:
                    previous_right_hand_gesture_id = right_hand_gesture_id

                # hand_sign_id = keypoint_classifier(pre_processed_landmark_list)
                hand_sign_id, confidence = keypoint_classifier(
                    pre_processed_landmark_list
                )
                hand_sign_name = keypoint_classifier_labels[hand_sign_id]
                # Assign gesture ID based on hand label
                if hand_label == "Left":
                    left_hand_sign_name = hand_sign_name
                elif hand_label == "Right":
                    right_hand_sign_name = hand_sign_name

                # Finger gesture classification
                finger_gesture_id = 0
                point_history_len = len(pre_processed_point_history_list)
                if point_history_len == (history_length * 2):
                    finger_gesture_id = point_history_classifier(
                        pre_processed_point_history_list
                    )

                # Calculates the gesture IDs in the latest detection
                finger_gesture_history.append(finger_gesture_id)
                most_common_fg_id = Counter(finger_gesture_history).most_common()

                finger_gesture_name = point_history_classifier_labels[
                    most_common_fg_id[0][0]
                ]

                # Drawing part
                debug_image = draw_bounding_rect(use_brect, debug_image, brect)
                debug_image = draw_landmarks(debug_image, landmark_list)
                debug_image = draw_info_text(
                    debug_image,
                    brect,
                    handedness,
                    keypoint_classifier_labels[hand_sign_id],
                    point_history_classifier_labels[most_common_fg_id[0][0]],
                )
        else:
            if gesture_counter > 0 or locked_in:
                print("\nNo gesture detected. Resetting...")
                if (
                    gesture_type
                    and gesture_subtype
                    # and left_hand_gesture_id != 8
                    # and left_hand_gesture_id != 10
                    # and right_hand_gesture_id != 11
                    # and right_hand_gesture_id != 7
                ):  # Send end command in the event we lose a gesture
                    # if (true
                    #     # left_hand_gesture_id != 8
                    #     # and left_hand_gesture_id != 10
                    #     # and right_hand_gesture_id != 11
                    #     # and right_hand_gesture_id != 7
                    # ):
                    tcp_client.send_gesture(f"{gesture_type} {gesture_subtype} end")
                    gesture_type = None
                    gesture_subtype = None
                print("RESETTING 2")
                point_history.append([0, 0])
                gesture_counter = 0
                # previous_hand_sign_id = None
                # previous_right_hand_gesture_id = None
                # previous_left_hand_gesture_id = None
                right_hand_gesture_id = None
                left_hand_gesture_id = None
                left_hand_sign_name = None
                right_hand_sign_name = None
                locked_in = False
                dual_gesture = False
            else:
                right_hand_gesture_id = None
                left_hand_gesture_id = None

        # debug_image = draw_point_history(debug_image, point_history)
        debug_image = draw_current_pointer_coordinates(debug_image, point_history)
        debug_image = draw_info(debug_image, fps, mode, number)

        # Screen reflection #############################################################
        cv.imshow("Hand Gesture Recognition", debug_image)

    tcp_client.close()
    cap.release()
    cv.destroyAllWindows()


if __name__ == "__main__":
    main()
