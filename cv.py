#!/usr/bin/env python3

# Copyright (c) FIRST and other WPILib contributors.
# Open Source Software; you can modify and/or share it under the terms of
# the WPILib BSD license file in the root directory of this project.

import json
import time
import sys

from cscore import CameraServer, VideoSource, UsbCamera, MjpegServer
from networktables import NetworkTablesInstance, NetworkTables

import cv2
import numpy as np

#   JSON format:
#   {
#       "team": <team number>,
#       "ntmode": <"client" or "server", "client" if unspecified>
#       "cameras": [
#           {
#               "name": <camera name>
#               "path": <path, e.g. "/dev/video0">
#               "pixel format": <"MJPEG", "YUYV", etc>   // optional
#               "width": <video mode width>              // optional
#               "height": <video mode height>            // optional
#               "fps": <video mode fps>                  // optional
#               "brightness": <percentage brightness>    // optional
#               "white balance": <"auto", "hold", value> // optional
#               "exposure": <"auto", "hold", value>      // optional
#               "properties": [                          // optional
#                   {
#                       "name": <property name>
#                       "value": <property value>
#                   }
#               ],
#               "stream": {                              // optional
#                   "properties": [
#                       {
#                           "name": <stream property name>
#                           "value": <stream property value>
#                       }
#                   ]
#               }
#           }
#       ]
#       "switched cameras": [
#           {
#               "name": <virtual camera name>
#               "key": <network table key used for selection>
#               // if NT value is a string, it's treated as a name
#               // if NT value is a double, it's treated as an integer index
#           }
#       ]
#   }

configFile = "/boot/frc.json"


class CameraConfig: pass


team = None
server = False
cameraConfigs = []
switchedCameraConfigs = []
cameras = []


def parseError(str):
    """Report parse error."""
    print("config error in '" + configFile + "': " + str, file=sys.stderr)


def readCameraConfig(config):
    """Read single camera configuration."""
    cam = CameraConfig()

    # name
    try:
        cam.name = config["name"]
    except KeyError:
        parseError("could not read camera name")
        return False

    # path
    try:
        cam.path = config["path"]
    except KeyError:
        parseError("camera '{}': could not read path".format(cam.name))
        return False

    # stream properties
    cam.streamConfig = config.get("stream")

    cam.config = config

    cameraConfigs.append(cam)
    return True


def readSwitchedCameraConfig(config):
    """Read single switched camera configuration."""
    cam = CameraConfig()

    # name
    try:
        cam.name = config["name"]
    except KeyError:
        parseError("could not read switched camera name")
        return False

    # path
    try:
        cam.key = config["key"]
    except KeyError:
        parseError("switched camera '{}': could not read key".format(cam.name))
        return False

    switchedCameraConfigs.append(cam)
    return True


def readConfig():
    """Read configuration file."""
    global team
    global server

    # parse file
    try:
        with open(configFile, "rt", encoding="utf-8") as f:
            j = json.load(f)
    except OSError as err:
        print("could not open '{}': {}".format(configFile, err), file=sys.stderr)
        return False

    # top level must be an object
    if not isinstance(j, dict):
        parseError("must be JSON object")
        return False

    # team number
    try:
        team = j["team"]
    except KeyError:
        parseError("could not read team number")
        return False

    # ntmode (optional)
    if "ntmode" in j:
        str = j["ntmode"]
        if str.lower() == "client":
            server = False
        elif str.lower() == "server":
            server = True
        else:
            parseError("could not understand ntmode value '{}'".format(str))

    # cameras
    try:
        cameras = j["cameras"]
    except KeyError:
        parseError("could not read cameras")
        return False
    for camera in cameras:
        if not readCameraConfig(camera):
            return False

    # switched cameras
    if "switched cameras" in j:
        for camera in j["switched cameras"]:
            if not readSwitchedCameraConfig(camera):
                return False

    return True


def startCamera(config):
    """Start running the camera."""
    print("Starting camera '{}' on {}".format(config.name, config.path))
    inst = CameraServer.getInstance()
    camera = UsbCamera(config.name, config.path)
    server = inst.startAutomaticCapture(camera=camera, return_server=True)

    camera.setConfigJson(json.dumps(config.config))
    camera.setConnectionStrategy(VideoSource.ConnectionStrategy.kKeepOpen)

    if config.streamConfig is not None:
        server.setConfigJson(json.dumps(config.streamConfig))

    return camera


def startSwitchedCamera(config):
    """Start running the switched camera."""
    print("Starting switched camera '{}' on {}".format(config.name, config.key))
    server = CameraServer.getInstance().addSwitchedCamera(config.name)

    def listener(fromobj, key, value, isNew):
        if isinstance(value, float):
            i = int(value)
            if i >= 0 and i < len(cameras):
                server.setSource(cameras[i])
        elif isinstance(value, str):
            for i in range(len(cameraConfigs)):
                if value == cameraConfigs[i].name:
                    server.setSource(cameras[i])
                    break

    NetworkTablesInstance.getDefault().getEntry(config.key).addListener(
        listener,
        NetworkTablesInstance.NotifyFlags.IMMEDIATE |
        NetworkTablesInstance.NotifyFlags.NEW |
        NetworkTablesInstance.NotifyFlags.UPDATE)

    return server


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        configFile = sys.argv[1]

    # read configuration
    if not readConfig():
        sys.exit(1)

    # start NetworkTables
    ntinst = NetworkTablesInstance.getDefault()
    if server:
        print("Setting up NetworkTables server")
        ntinst.startServer()
    else:
        print("Setting up NetworkTables client for team {}".format(team))
        ntinst.startClientTeam(team)
        ntinst.startDSClient()

    # start cameras
    for config in cameraConfigs:
        cameras.append(startCamera(config))

    # start switched cameras
    for config in switchedCameraConfigs:
        startSwitchedCamera(config)

    width = 640
    height = 480

    # Table for vision output information
    vision_nt = NetworkTables.getTable('Vision')

    # Wait for NetworkTables to start
    time.sleep(0.5)

    input_img = np.zeros(shape=(480, 640, 3), dtype=np.uint8)

    print("Doing computer vision things")

    focal = 60 * 94 / 8.25
    focalBall = 59 * 69 / 7

    camera_index = vision_nt.getNumber("camera_index", 0)

    input_stream = CameraServer.getInstance().getVideo(camera=cameras[int(camera_index)])
    output_stream = CameraServer.getInstance().putVideo('Processed', width, height)

    # loop forever
    while True:
        start_time = time.time()
        temp_camera_index = vision_nt.getNumber("camera_index", 0)
        if temp_camera_index != camera_index and temp_camera_index<3 and temp_camera_index>=0:
            camera_index = temp_camera_index
            input_stream = CameraServer.getInstance().getVideo(camera=cameras[int(camera_index)])

        frame_time, input_img = input_stream.grabFrame(input_img)
        output_img = np.copy(input_img)

        # Notify output of error and skip iteration
        if frame_time == 0:
            output_stream.notifyError(input_stream.getError())
            continue

        # Convert to HSV and threshold image
        hsv_img = cv2.cvtColor(input_img, cv2.COLOR_BGR2HSV)
        binary_img = np.zeros(shape=(480, 640, 3))

        # inside:
        if camera_index == 0:
            binary_img = cv2.inRange(hsv_img, (0, 85, 45), (75, 255, 255)) 
           # binary_img = cv2.inRange(hsv_img, (65, 65, 100), (85, 255, 255))
        elif (camera_index == 1 or camera_index == 2):
            binary_img = cv2.inRange(hsv_img, (20, 160, 70), (43, 255, 255))

        # outside:
        # binary_img = cv2.inRange(hsv_img, (55, 20, 20), (100, 255, 255))

        _, contour_list, _ = cv2.findContours(binary_img, mode=cv2.RETR_EXTERNAL, method=cv2.CHAIN_APPROX_SIMPLE)

        x_list = []
        y_list = []
        area_list = []
        width_list = []
        distance = []

        for contour in contour_list:

            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)
            if(perimeter==0):
                continue
            circularity = 4 * 3.1415926535 * area / (perimeter * perimeter)
            # Ignore small contours that could be because of noise/bad thresholding
            if area < 50 or (area<150 and camera_index==2):
                continue
            elif camera_index == 0 and ((not 0.5 < circularity < 1.5) or (area > 100000)):
                continue
            # elif camera_index == 0 and ((not (0.8 < circularity < 1.5) or area > 10000)):
            #     continue

            # cv2.drawContours(output_img, contour, -1, color=(255, 255, 255), thickness=-1)

            rect = cv2.minAreaRect(contour)
            center, size, angle = rect
            center = [int(dim) for dim in center]  # Convert to int so we can draw

            # if(camera_index == 0 and (size[0] < 50 or size[1] < 30)):
            #     continue
            # elif ((camera_index == 1 or camera_index == 2) and (size[1]/size[0]<=0.5 or size[1]/size[0]>=2)):
            #     continue

            # Draw rectangle and circle
            # cv2.drawContours(output_img, np.int0(cv2.boxPoints(rect)), -1, (0, 0, 255), 2)
            cv2.drawContours(output_img, [contour], -1, (212, 0, 255), 5)
            cv2.circle(output_img, center=tuple(center), radius=4, color=(0, 0, 255), thickness=-1)

            target_width = size[0]

            x_list.append((center[0] - width / 2) / (width / 2))
            y_list.append((center[1] - height / 2) / (height / 2))
            area_list.append(area)
            width_list.append(target_width)
            if(camera_index == 0):
                distance.append(8.25 * focal / target_width)
            else:
                distance.append(7 * focalBall / target_width)

        vision_nt.putNumberArray('x_pos', x_list)
        vision_nt.putNumberArray('y_pos', y_list)
        vision_nt.putNumberArray('area', area_list)
        vision_nt.putNumberArray('width', width_list)
        vision_nt.putNumberArray('distance', distance)

        processing_time = time.time() - start_time
        fps = 1 / processing_time
        cv2.putText(output_img, str(round(fps, 1)) + " fps", (0, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255))
        if (camera_index == 0 and len(distance) > 0):
            cv2.putText(output_img, str(round(distance[0], 1)) + " in", (0, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                        (255, 255, 255))
        output_stream.putFrame(output_img)
