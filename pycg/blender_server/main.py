"""
Copyright 2024 by Jiahui Huang. All rights reserved.
This file is part of PyCG toolbox and is released under "MIT License Agreement".
Please see the LICENSE file that should have been included as part of this package.
"""

import time
import bpy
import argparse
from bpy import data as D
import logging
import uuid
import sys
from multiprocessing.managers import BaseManager
import queue
from logging import StreamHandler
from pathlib import Path
import numpy as np

sys.path.append(str(Path(__file__).parent))
from assets import AssetManager, register_assets
from style import register_style


class BlenderTextHandler(StreamHandler):
    def __init__(self, text):
        super().__init__()
        self.text = text

    def emit(self, record):
        msg = self.format(record)
        self.text.write(msg + '\n')


def get_object(name):
    if name.lower() == 'relative_camera':
        target_object = bpy.context.scene.camera
    elif name.lower() == "camera_base":
        target_object = bpy.data.objects["camera_base"]
    else:
        target_object = AssetManager.data[name].object
    return target_object


def rebuild_world_surface():
    cur_world = bpy.context.scene.world
    nodes = cur_world.node_tree.nodes
    links = cur_world.node_tree.links
    if bpy.data.images.get("EnvTex") is not None:
        bpy.data.images.remove(bpy.data.images.get("EnvTex"), do_unlink=True)
    nodes.clear()
    return nodes, links


def handle_cmds(msg):
    """Handle incoming commands from the client.
    
    Main command handler that processes different types of commands:
    - entity: Create geometry objects
    - camera: Update camera parameters
    - light: Create/update lights
    - render: Render scene
    - clear: Clear scene
    - etc.

    Args:
        msg (dict): Command message from client. 'cmd' is the command type, and the following keys are command-specific.

    Returns:
        dict: Response message to send back to client
    """
    if msg['cmd'] == 'entity':
        logging.info(f"Command entity: draw {msg['geometry_type']}.")
        geom_obj = AssetManager.create_entity(msg) # will create point cloud or triangle mesh in blender
        geom_obj.rotation_mode = 'QUATERNION'
        geom_obj.rotation_quaternion = msg['pose'][3:]
        geom_obj.location = msg['pose'][:3]
        return {
            'result': 'created',
            'uuid': geom_obj.pycg_asset.uuid
        }
    
    elif msg['cmd'] == 'clear':
        # Clear assets
        AssetManager.clear_all()
        # Clear lights
        for light_data in bpy.data.lights:
            bpy.data.lights.remove(light_data, do_unlink=True)
        # Clear camera and base animations
        get_object('relative_camera').animation_data_clear()
        get_object('camera_base').animation_data_clear()
        # Clear envmaps
        nodes, links = rebuild_world_surface()
        background_node = nodes.new(type='ShaderNodeBackground')
        output_node = nodes.new(type='ShaderNodeOutputWorld')
        links.new(background_node.outputs[0], output_node.inputs[0])
        return {'result': 'success'}
    
    elif msg['cmd'] == 'camera':
        cur_scene = bpy.context.scene
        if 'pose' in msg.keys():
            cam_ext = np.asarray(msg['pose'][0])
            cur_scene.camera.location = cam_ext[0:3]
            cur_scene.camera.rotation_mode = 'QUATERNION'
            cur_scene.camera.rotation_quaternion = cam_ext[3:7]
        if 'intrinsic' in msg.keys():
            cam_intr = np.asarray(msg['intrinsic'])
            cur_scene.render.resolution_x = int(cam_intr[0])
            cur_scene.render.resolution_y = int(cam_intr[1])
            cur_scene.camera.data.shift_x = cam_intr[2]
            cur_scene.camera.data.shift_y = cam_intr[3]
            cur_scene.camera.data.angle = cam_intr[4]
            cur_scene.camera.data.clip_start = 0.1
            cur_scene.camera.data.clip_end = 1000.0

        return {'result': 'success'}
    
    elif msg['cmd'] == 'get_entity':
        logging.info(f"Command get_entity: get uuid {msg['uuid']}")
        res = AssetManager.get_entity(msg['uuid'])
        res.update({'result': 'got'})
        return res
    
    elif msg['cmd'] == 'light':
        light_id = msg.get('uuid', str(uuid.uuid1()))
        light_name = f"Light-{light_id}"
        light_data = bpy.data.lights.new(name=light_name, type=msg['type'])
        light_data.energy = msg['energy']

        if msg['type'] == 'SUN':
            light_data.angle = msg['angle']
        elif msg['type'] == 'POINT' or msg['type'] == 'SPOT':
            light_data.shadow_soft_size = msg['radius']
        elif msg['type'] == 'AREA':
            light_data.size = msg['size']

        light_object = bpy.data.objects.new(name=light_name, object_data=light_data)
        bpy.context.collection.objects.link(light_object)
        light_object.location = msg['pos']
        light_object.rotation_mode = 'QUATERNION'
        light_object.rotation_quaternion = msg['rot']
        return {
            'result': 'created',
            'uuid': light_id
        }
    
    elif msg['cmd'] == 'entity_pose':
        target_object = get_object(msg['uuid'])
        if msg['rotation_mode'] is not None:
            target_object.rotation_mode = msg['rotation_mode']
            if msg['rotation_mode'] == 'QUATERNION':
                for q in range(4):
                    target_object.rotation_quaternion[q] = msg['rotation_value'][q]
            elif msg['rotation_mode'] == 'AXIS_ANGLE':
                for q in range(4):
                    target_object.rotation_axis_angle[q] = msg['rotation_value'][q]
            else:
                for q in range(3):
                    target_object.rotation[q].rotation_euler[q] = msg['rotation_value'][q]
        if msg['location_value'] is not None:
            loc = msg['location_value']
            target_object.location = (loc[0], loc[1], loc[2])
        return {'result': 'success'}

    elif msg['cmd'] == 'add_keyframe':
        if msg['uuid'].lower() == 'camera':
            cur_scene = bpy.context.scene
            if msg['attribute'].lower() == 'pose':
                cam_ext = np.asarray(msg['value'])
                cur_scene.camera.location = cam_ext[0:3]
                cur_scene.camera.rotation_mode = 'QUATERNION'
                cur_scene.camera.rotation_quaternion = cam_ext[3:7]
                cur_scene.camera.keyframe_insert(data_path="location", frame=msg['frame'])
                cur_scene.camera.keyframe_insert(data_path="rotation_quaternion", frame=msg['frame'])
        return {'result': 'added'}
    
    elif msg['cmd'] == 'add_animation_fcurve':
        target_object = get_object(msg['uuid'])
        if target_object.animation_data is None:
            target_object.animation_data_create()

        if target_object.animation_data.action is None:
            target_action = bpy.data.actions.new(f'{target_object.name}-Action')
            target_object.animation_data.action = target_action
        else:
            target_action = target_object.animation_data.action

        fcurve = target_action.fcurves.new(data_path=msg['data_path'], index=msg['index'])
        fcurve.auto_smoothing = 'NONE'

        for v in msg['values']:
            if msg['mode'] == 'constant':
                kf = fcurve.keyframe_points.insert(frame=v[0], value=v[1])
                kf.interpolation = 'CONSTANT'
            elif msg['mode'] == 'linear':
                kf = fcurve.keyframe_points.insert(frame=v[0], value=v[1])
                kf.interpolation = 'LINEAR'
            else:   # Bezier
                kf = fcurve.keyframe_points.insert(frame=v[1][0], value=v[1][1])
                kf.interpolation = 'BEZIER'
                kf.handle_left = v[0]
                kf.handle_left_type = 'ALIGNED'     # Should be ok if we use AUTO_CLAMPED
                kf.handle_right = v[2]
                kf.handle_right_type = 'ALIGNED'

        return {'result': 'added'}

    elif msg['cmd'] == 'envmap':
        nodes, links = rebuild_world_surface()

        # Create new image containing the env texture
        env_tex_node = nodes.new(type='ShaderNodeTexEnvironment')
        env_tex_data = msg['data']
        assert env_tex_data.ndim == 3 and env_tex_data.shape[2] == 3
        env_tex_data = np.concatenate([env_tex_data, np.ones_like(env_tex_data[:, :, 1:2])], axis=2).astype(np.float64)
        env_tex_data = env_tex_data[::-1, ...]
        new_tex = bpy.data.images.new(name=f"EnvTex", width=env_tex_data.shape[1], height=env_tex_data.shape[0],
                                      alpha=True, float_buffer=True)
        new_tex.file_format = 'HDR'
        new_tex.pixels = env_tex_data.ravel()
        env_tex_node.image = new_tex

        tex_coord_node = nodes.new(type='ShaderNodeTexCoord')

        tex_mapping_node = nodes.new(type='ShaderNodeMapping')
        tex_mapping_node.inputs[2].default_value = msg['rotation']

        output_node = nodes.new(type='ShaderNodeOutputWorld')
        links.new(tex_coord_node.outputs[0], tex_mapping_node.inputs[0])
        links.new(tex_mapping_node.outputs[0], env_tex_node.inputs[0])
        links.new(env_tex_node.outputs[0], output_node.inputs[0])

        logging.info(f"Command envmap: added! {env_tex_data.shape}")

        return {'result': 'success'}

    elif msg['cmd'] == 'render':
        bpy.context.scene.cycles.samples = msg['quality']
        bpy.context.scene.render.filepath = msg['path']
        bpy.ops.render.render(write_still=True)
        return {
            'result': 'rendered'
        }

    elif msg['cmd'] == 'eval':
        exec(msg['script'])
        return {'result': 'success'}

    elif msg['cmd'] == 'save':
        bpy.ops.wm.save_as_mainfile(msg['path'])
        return {'result': 'success'}

    elif msg['cmd'] == 'detach':
        raise ConnectionRefusedError

    else:
        raise NotImplementedError


class ClientOperator(bpy.types.Operator):
    """Blender operator that handles client communication.
    
    Runs as a modal operator to:
    1. Listen for incoming commands from client
    2. Process commands via handle_cmds()
    3. Send back responses
    
    The operator runs continuously until the client detaches.
    """
    bl_idname = "client.run"
    bl_label = "Run pycg Blender Client"

    _timer = None
    global_res_queue = None

    def __init__(self):
        """Initialize the client operator.
        
        Sets up queues for inter-process communication between Blender and the client.
        Creates a BaseManager instance to manage the shared queues.
        """
        res_queue = queue.Queue()
        cmd_queue = queue.Queue()
        BaseManager.register('res_queue', callable=lambda: res_queue)
        BaseManager.register('cmd_queue', callable=lambda: cmd_queue)
        self.conn_manager = BaseManager(address=('', args.port), authkey=b'pycg.blender')
        self.res_queue = None
        self.cmd_queue = None

    def modal(self, context, event):
        """This function that will keep being run to handle events until it returns {'FINISHED'} or {'CANCELLED'}.
        Modal operators run every time a new event is detected, such as a mouse click or key press. 
        Conversely, when no new events are detected, the modal operator will not run. 
        Modal operators are especially useful for interactive tools, an operator can have its own state 
        where keys toggle options as the operator runs. Grab, Rotate, Scale, and Fly-Mode are examples of modal operators.

        Operator.invoke is used to initialize the operator as being active by returning {'RUNNING_MODAL'}, initializing the modal loop.
        
        Args:
            context: Blender context
            event: Current event
            
        Returns:
            set: Operator return state - CANCELLED if reference error occurs
        """
        try:
            return self._modal(context, event)
        except ReferenceError:
            return {'CANCELLED'}

    def _modal(self, context, event):
        """Internal modal function that handles command processing at every timer event.
        timer event is set by self._timer = wm.event_timer_add(0.1, window=context.window)
        
        Args:
            context: Blender context
            event: Current event
            
        Returns:
            set: Operator return state - PASS_THROUGH to keep running, CANCELLED on detach
        """
        if event.type == 'TIMER':
            try:
                new_command = self.cmd_queue.get_nowait()
            except queue.Empty:
                return {'PASS_THROUGH'}
            try:
                print("The new command is: ", new_command)
                res = handle_cmds(new_command)
            except KeyError as e:
                res = {'result': 'failed'}
            except ConnectionRefusedError as e:
                try:
                    self.conn_manager.shutdown()
                except ReferenceError:
                    pass
                context.window_manager.event_timer_remove(self._timer)
                logging.info("Master detached.")
                return {'CANCELLED'}
            if res is not None:
                self.res_queue.put(res)

        return {'PASS_THROUGH'}

    def invoke(self, context, event):
        """Operator.invoke is used to initialize the operator from the context at the moment the operator is called. 
        invoke() is typically used to assign properties which are then used by execute(). 
        Some operators don’t have an execute() function, removing the ability to be repeated from a script or macro.
        
        Args:
            context: Blender context
            event: Current event
            
        Returns:
            set: RUNNING_MODAL to start modal execution
        """
        self.conn_manager.start()
        self.res_queue = self.conn_manager.res_queue()
        self.cmd_queue = self.conn_manager.cmd_queue()
        ClientOperator.global_res_queue = self.res_queue

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)

        # Add a modal handler to the window manager, 
        # for the given modal operator (called by invoke() with self, just before returning {‘RUNNING_MODAL’})
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'} 

    def cancel(self, context):
        """Called when the operator is cancelled.
        
        Args:
            context: Blender context
        """
        pass


class PYCG_OT_host_notify(bpy.types.Operator):
    """Operator to send notifications back to the client.
    
    Used to notify the client when certain Blender operations complete.
    """
    bl_idname = "pycg.host_notify"
    bl_label = "Notify Host"
    bl_description = "Send a notification message to host"

    @classmethod
    def poll(cls, context):
        return ClientOperator.global_res_queue is not None

    def execute(self, context):
        ClientOperator.global_res_queue.put({'result': 'notify'})
        logging.info("Host notified.")
        return {'FINISHED'}


def init_env():
    """Initialize the Blender environment.
    
    Sets up:
    - Empty scene
    - Camera and base object
    - Cycles render engine
    - GPU/CPU compute devices
    - Workspace layout
    - Logging
    """
    # Empty scene
    D.meshes.remove(D.meshes["Cube"], do_unlink=True)
    D.lights.remove(D.lights["Light"], do_unlink=True)

    # Link the camera to an empty object
    camera_base = bpy.data.objects.new(name='camera_base', object_data=None)
    camera_base.empty_display_size = 1.0
    camera_base.empty_display_type = 'PLAIN_AXES'
    bpy.context.collection.objects.link(camera_base)
    bpy.context.scene.camera.parent = camera_base

    # Renderer and Color specs
    D.scenes[0].render.engine = 'CYCLES'

    if not args.no_cuda:
        D.scenes[0].cycles.device = 'GPU'
        if args.no_optix:
            # Blender 2.x may not support the full feature set
            bpy.context.preferences.addons["cycles"].preferences.compute_device_type = "CUDA"
        else:
            # Blender 3 now supports RTX (using OptiX)
            bpy.context.preferences.addons["cycles"].preferences.compute_device_type = "OPTIX"

    # Let blender refresh GPU devices
    bpy.context.preferences.addons["cycles"].preferences.get_devices()
    all_devices = bpy.context.preferences.addons["cycles"].preferences.devices

    if args.no_cuda:
        for d in all_devices:
            if d.type == 'CPU':
                d.use = True
            else:
                d.use = False
    else:
        for d in all_devices:
            d.use = True
    
    for d in all_devices:
        if d.use:
            print("Rendering using devices:", d["name"])
        else:
            print("Not using devices:", d["name"])

    D.scenes[0].cycles.use_denoising = True
    D.scenes[0].view_settings.view_transform = 'Standard'

    # Init workspace by changing Layout screen
    # workspace contains screens, screens contains areas (of different functions)
    # and areas contains regions (e.g. transformation panel)
    if not bpy.app.background:
        split_context = {
            'area': [p for p in D.workspaces['Layout'].screens[0].areas if p.type == 'VIEW_3D'][0],
            'screen': D.workspaces['Layout'].screens[0]
        }
        bpy.ops.screen.area_split(split_context, direction='VERTICAL', factor=0.3)

        new_area = split_context['screen'].areas[-1]
        new_area.type = 'TEXT_EDITOR'

        logger_text = bpy.data.texts.new("log")
        new_area.spaces[0].text = logger_text

        # Set logging info to logger text.
        handler = BlenderTextHandler(logger_text)
        formatter = logging.Formatter('%(asctime)s: %(message)s', datefmt='%m-%d %H:%M')
        handler.setFormatter(formatter)
        logging.getLogger('').addHandler(handler)

    logging.basicConfig(level=logging.INFO)
    logging.info("Environment Initialized...")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, help='Port to listen on.')
    parser.add_argument('--no-cuda', action='store_true', help='Use CPU only.')
    parser.add_argument('--no-optix', action='store_true', help='Use CUDA instead of OptiX.')
    argv = sys.argv[sys.argv.index("--") + 1:]  # get all args after "--"
    args = parser.parse_args(argv)

    init_env()

    register_assets()
    register_style()

    # if not the background mode, the server will always run.
    if not bpy.app.background:
        bpy.utils.register_class(ClientOperator)
        bpy.utils.register_class(PYCG_OT_host_notify)
        bpy.ops.client.run('INVOKE_DEFAULT') # this will call the invoke function in ClientOperator

    # if the background mode, use a while loop to keep the server running.
    else:
        res_queue = queue.Queue()
        cmd_queue = queue.Queue()
        BaseManager.register('res_queue', callable=lambda: res_queue)
        BaseManager.register('cmd_queue', callable=lambda: cmd_queue)
        conn_manager = BaseManager(address=('', args.port), authkey=b'pycg.blender')
        conn_manager.start()

        res_queue = conn_manager.res_queue()
        cmd_queue = conn_manager.cmd_queue()

        while True:
            try:
                new_command = cmd_queue.get_nowait()
            except queue.Empty:
                time.sleep(0.1)
                continue
            try:
                res = handle_cmds(new_command)
            except KeyError as e:
                logging.error(e)
                res = {'result': 'failed'}
            except ConnectionRefusedError as e:
                try:
                    conn_manager.shutdown()
                except ReferenceError:
                    pass
                break

            if res is not None:
                res_queue.put(res)
