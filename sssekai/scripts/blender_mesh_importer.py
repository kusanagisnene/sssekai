# Config
SHADER_BLEND_FILE = r'C:\Users\Huang\sssekai\sssekai\scripts\assets\SekaiShaderStandalone.blend'
PYTHON_PACKAGES_PATH = r'C:\Users\Huang\AppData\Local\Programs\Python\Python310\Lib\site-packages'
import sys,os,math
try:
    import bpy
    import bpy_extras
    import bmesh
    from mathutils import Matrix, Quaternion as BlenderQuaternion, Vector, Euler
    # HACK: to get blender to use the system's python packges
    sys.path.append(PYTHON_PACKAGES_PATH)    
    BLENDER = True
except ImportError:
    # Stubs for debugging outside blender's python
    # pip install fake-bpy-module
    class Matrix: pass
    class BlenderQuaternion: pass
    class Vector:pass
    BLENDER = False
# Coordinate System | Forward |  Up  |  Left
# Unity:   LH, Y Up |   Z     |   Y  |   X
# Blender: RH, Z Up |  -Y     |   Z  |  -X
def swizzle_vector3(X,Y,Z):    
    return Vector((-X,-Z,Y))
def swizzle_vector(vec):
    return swizzle_vector3(vec.X, vec.Y, vec.Z)
def swizzle_euler3(X,Y,Z):
    return Euler((X,Z,-Y),'XYZ')
def swizzle_euler(euler, isDegrees = True):
    if isDegrees:  
        return swizzle_euler3(math.radians(euler.X),math.radians(euler.Y),math.radians(euler.Z))
    else:
        return swizzle_euler3(euler.X, euler.Y, euler.Z)
def swizzle_quaternion4(X,Y,Z,W):
    return BlenderQuaternion((W,X,Z,-Y)) # conjugate (W,-X,-Z,Y)
def swizzle_quaternion(quat):
    return swizzle_quaternion4(quat.X, quat.Y, quat.Z, quat.W)
import json
import tempfile
from typing import List, Dict, Tuple
from collections import defaultdict
from dataclasses import dataclass
import zlib
# Used for bone path (boneName) and blend shape name inverse hashing
def get_name_hash(name : str):
    return zlib.crc32(name.encode('utf-8'))
# The tables stored in the mesh's Custom Properties. Used by the animation importer.
KEY_BONE_NAME_HASH_TBL = 'sssekai_bone_name_hash_tbl' # Bone *full path hash* to bone name (Vertex Group name in blender lingo)
KEY_SHAPEKEY_NAME_HASH_TBL = 'sssekai_shapekey_name_hash_tbl' # ShapeKey name hash to ShapeKey names
KEY_BINDPOSE_TRANS = 'sssekai_bindpose_trans' # Bone name to bind pose translation
KEY_BINDPOSE_QUAT = 'sssekai_bindpose_quat' # Bone name to bind pose quaternion
# UnityPy deps
from UnityPy import Environment, config
from UnityPy.enums import ClassIDType
from UnityPy.classes import Mesh, SkinnedMeshRenderer, MeshRenderer, GameObject, Transform, Texture2D, Material
from UnityPy.math import Vector3, Quaternion as UnityQuaternion
# SSSekai deps
from sssekai.unity import SEKAI_UNITY_VERSION # XXX: expandusr does not work in Blender Python!
from sssekai.unity.AnimationClip import Track, read_animation, Animation, TransformType
config.FALLBACK_UNITY_VERSION = SEKAI_UNITY_VERSION
from sssekai.unity.AssetBundle import load_assetbundle
import math
@dataclass
class Bone:
    name : str
    localPosition : Vector3
    localRotation : UnityQuaternion
    localScale : Vector3
    # Hierarchy. Built after asset import
    children : list # Bone
    global_path : str = ''
    global_transform : Matrix = None
    # Helpers
    def get_blender_local_position(self):
        return swizzle_vector(self.localPosition)
    def get_blender_local_rotation(self):
        return swizzle_quaternion(self.localRotation)
    def to_translation_matrix(self):
        return Matrix.Translation(swizzle_vector(self.localPosition))
    def to_trs_matrix(self):
        return Matrix.LocRotScale(
            swizzle_vector(self.localPosition),
            swizzle_quaternion(self.localRotation),
            Vector3(1,1,1)
        )
    def dfs_generator(self, root = None):
        def dfs(bone : Bone, parent : Bone = None, depth = 0):
            yield parent, bone, depth
            for child in bone.children:
                yield from dfs(child, bone, depth + 1)
        yield from dfs(root or self)        
    # Extra
    edit_bone = None # Blender Bone
@dataclass
class Armature:
    name : str
    skinned_mesh_gameobject : GameObject = None
    root : Bone = None
    # Tables
    bone_path_hash_tbl : Dict[int,Bone] = None
    bone_name_tbl : Dict[str,Bone] = None
    # Helpers
    def get_bone_by_path(self, path : str):
        return self.bone_path_hash_tbl[get_name_hash(path)]
    def get_bone_by_name(self, name : str):
        return self.bone_name_tbl[name]
    def debug_print_bone_hierarchy(self):
        for parent, child, depth in self.root.dfs_generator():
            print('\t' * depth, child.name)
def search_env_meshes(env : Environment):
    '''(Partially) Loads the UnityPy Environment for further Mesh processing

    Args:
        env (Environment): UnityPy Environment

    Returns:
        Tuple[List[GameObject], Dict[str,Armature]]: Static Mesh GameObjects and Armatures
    '''
    # Collect all static meshes and skinned meshes's *root transform* object
    # UnityPy does not construct the Bone Hierarchy so we have to do it ourselves
    static_mesh_gameobjects : List[GameObject] = list() # No extra care needed
    transform_roots = []
    for obj in env.objects:
        data = obj.read()
        if obj.type == ClassIDType.GameObject and getattr(data,'m_MeshRenderer',None):
            static_mesh_gameobjects.append(data)
        if obj.type == ClassIDType.Transform:
            if hasattr(data,'m_Children') and not data.m_Father.path_id:
                transform_roots.append(data)
    # Collect all skinned meshes as Armature[s]
    # Note that Mesh maybe reused across Armatures, but we don't care...for now
    armatures = []
    for root in transform_roots:
        armature = Armature(root.m_GameObject.read().m_Name)
        armature.bone_path_hash_tbl = dict()
        armature.bone_name_tbl = dict()
        def dfs(root : Transform, parent : Bone = None):
            gameObject = root.m_GameObject.read()
            name = gameObject.m_Name        
            if getattr(gameObject,'m_SkinnedMeshRenderer',None):
                armature.skinned_mesh_gameobject = gameObject
            path_from_root = ''
            if parent and parent.global_path:
                path_from_root = parent.global_path + '/' + name
            elif parent:
                path_from_root = name
            bone = Bone(
                name,
                root.m_LocalPosition,
                root.m_LocalRotation,
                root.m_LocalScale,
                list(),
                path_from_root
            )
            armature.bone_name_tbl[name] = bone
            armature.bone_path_hash_tbl[get_name_hash(path_from_root)] = bone
            if not parent:
                armature.root = bone
            else:
                parent.children.append(bone)
            for child in root.m_Children:
                dfs(child.read(), bone)
        dfs(root)    
        if armature.skinned_mesh_gameobject:
            armatures.append(armature)
    return static_mesh_gameobjects, armatures
def search_env_animations(env : Environment):
    '''Searches the Environment for AnimationClips

    Args:
        env (Environment): UnityPy Environment

    Returns:
        List[AnimationClip]: AnimationClips
    '''
    animations = []
    for asset in env.assets:
        for obj in asset.get_objects():
            data = obj.read()
            if obj.type == ClassIDType.AnimationClip:
                animations.append(data)
    return animations

def import_mesh(name : str, data: Mesh, skinned : bool = False, bone_path_tbl : Dict[str,Bone] = None):
    '''Imports the mesh data into blender.

    Takes care of the following:
    - Vertices (Position + Normal) and indices (Trig Faces)
    - UV Map
    Additonally, for Skinned meshes:
    - Bone Indices + Bone Weights
    - Blend Shape / Shape Keys

    Args:
        name (str): Name for the created Blender object
        data (Mesh): Source UnityPy Mesh data
        skinned (bool, optional): Whether the Mesh has skinning data, i.e. attached to SkinnedMeshRenderer. Defaults to False.

    Returns:
        Tuple[bpy.types.Mesh, bpy.types.Object]: Created mesh and its parent object
    '''
    print('* Importing Mesh', data.name, 'Skinned=', skinned)
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new(name=data.name)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    bm = bmesh.new()
    vtxFloats = int(len(data.m_Vertices) / data.m_VertexCount)
    normalFloats = int(len(data.m_Normals) / data.m_VertexCount)
    uvFloats = int(len(data.m_UV0) / data.m_VertexCount)
    colorFloats = int(len(data.m_Colors) / data.m_VertexCount)
    # Bone Indices + Bone Weights
    deform_layer = None
    if skinned:
        for boneHash in data.m_BoneNameHashes:
            # boneHash is the CRC32 hash of the full bone path
            # i.e Position/Hips/Spine/Spine1/Spine2/Neck/Head
            group_name = bone_path_tbl[boneHash].name   
            obj.vertex_groups.new(name=group_name)
        deform_layer = bm.verts.layers.deform.new()
        # Animations uses the hash to identify the bone
        # so this has to be stored in the metadata as well
        mesh[KEY_BONE_NAME_HASH_TBL] = json.dumps({k:v.name for k,v in bone_path_tbl.items()},ensure_ascii=False)
    # Vertex position & vertex normal (pre-assign)
    for vtx in range(0, data.m_VertexCount):        
        vert = bm.verts.new(swizzle_vector3(
            data.m_Vertices[vtx * vtxFloats], # x,y,z
            data.m_Vertices[vtx * vtxFloats + 1],
            data.m_Vertices[vtx * vtxFloats + 2]            
        ))
        # Blender always generates normals automatically
        # Custom normals needs a bit more work
        # See below for normals_split... calls
        # XXX why is this flipped?
        vert.normal = swizzle_vector3(
            -1 * data.m_Normals[vtx * normalFloats],
            -1 * data.m_Normals[vtx * normalFloats + 1],
            -1 * data.m_Normals[vtx * normalFloats + 2]
        )
        if deform_layer:
            for i in range(4):
                skin = data.m_Skin[vtx]
                if skin.weight[i] == 0:
                    break # Weight is sorted
                vertex_group_index = skin.boneIndex[i]
                vert[deform_layer][vertex_group_index] = skin.weight[i]
    bm.verts.ensure_lookup_table()
    # Indices
    for idx in range(0, len(data.m_Indices), 3):
        face = bm.faces.new([bm.verts[data.m_Indices[idx + j]] for j in range(3)])
        face.smooth = True
    bm.to_mesh(mesh)
    # UV Map
    uv_layer = mesh.uv_layers.new()
    mesh.uv_layers.active = uv_layer
    for face in mesh.polygons:
        for vtx, loop in zip(face.vertices, face.loop_indices):
            uv_layer.data[loop].uv = (
                data.m_UV0[vtx * uvFloats], 
                data.m_UV0[vtx * uvFloats + 1]
            )
    # Vertex Color
    vertex_color = mesh.color_attributes.new(name='Vertex Color',type='FLOAT_COLOR',domain='POINT')
    for vtx in range(0, data.m_VertexCount):
        color = [data.m_Colors[vtx * colorFloats + i] for i in range(colorFloats)]
        vertex_color.data[vtx].color = color
    # Assign vertex normals
    mesh.create_normals_split()
    normals = [(0,0,0) for l in mesh.loops]
    for i, loop in enumerate(mesh.loops):
        normal = bm.verts[loop.vertex_index].normal
        normal.normalize()
        normals[i] = normal
    mesh.normals_split_custom_set(normals)
    mesh.use_auto_smooth = True   
    # Blend Shape / Shape Keys
    if data.m_Shapes.channels:
        obj.shape_key_add(name="Basis")
        keyshape_hash_tbl = dict()
        for channel in data.m_Shapes.channels:
            shape_key = obj.shape_key_add(name=channel.name)
            keyshape_hash_tbl[channel.nameHash] = channel.name
            for frameIndex in range(channel.frameIndex, channel.frameIndex + channel.frameCount):
                # fullWeight = mesh_data.m_Shapes.fullWeights[frameIndex]
                shape = data.m_Shapes.shapes[frameIndex]
                for morphedVtxIndex in range(shape.firstVertex,shape.firstVertex + shape.vertexCount):
                    morpedVtx = data.m_Shapes.vertices[morphedVtxIndex]
                    targetVtx : bpy.types.ShapeKeyPoint = shape_key.data[morpedVtx.index]
                    targetVtx.co += swizzle_vector(morpedVtx.vertex)                    
        # Like boneHash, do the same thing with blend shapes
        mesh[KEY_SHAPEKEY_NAME_HASH_TBL] = json.dumps(keyshape_hash_tbl,ensure_ascii=False)
    bm.free()      
    return mesh, obj
def import_armature(name : str, data : Armature):
    '''Imports the Armature data generated into blender

    NOTE: Unused bones will not be imported since they have identity transforms and thus
    cannot have their own head-tail vectors. It's worth noting though that they won't affect
    the mesh anyway.

    Args:
        name (str): Armature Object name
        data (Armature): Armature as genereated by previous steps
    
    Returns:
        Tuple[bpy.types.Armature, bpy.types.Object]: Created armature and its parent object
    '''
    armature = bpy.data.armatures.new(name)
    armature.display_type = 'STICK'
    armature.relation_line_position = 'HEAD'

    obj = bpy.data.objects.new(name, armature)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    # HACK: *Seems like* the only useful root bone is 'Position' (which is the root of the actual skeleton)
    for bone in data.root.children:
        bone : Bone
        if bone.name == 'Position':
            print('* Found valid armature root', bone.name, 'at', bone.global_path, 'with', len(bone.children), 'children')
            bone.global_transform = Matrix.Identity(4)
            # Build global transforms
            # I think using the inverse of m_BindPose could work too
            # but I kinda missed it until I implemented all this so...            
            for parent, child, _ in bone.dfs_generator():
                if parent:
                    child.global_transform = parent.global_transform @ child.to_trs_matrix()
                else:
                    child.global_transform = child.to_trs_matrix()
            # Build bone hierarchy in blender
            for parent, child, _ in bone.dfs_generator():
                ebone = armature.edit_bones.new(child.name)
                ebone.use_local_location = True
                ebone.use_relative_parent = False                
                ebone.use_connect = False
                ebone.use_deform = True
                ebone[KEY_BINDPOSE_TRANS] = [v for v in child.get_blender_local_position()]
                ebone[KEY_BINDPOSE_QUAT] = [v for v in child.get_blender_local_rotation()]
                child.edit_bone = ebone
                # Treat the joints as extremely small bones
                # The same as https://github.com/KhronosGroup/glTF-Blender-IO/blob/2debd75ace303f3a3b00a43e9d7a9507af32f194/addons/io_scene_gltf2/blender/imp/gltf2_blender_node.py#L198
                # TODO: Alternative shapes for bones                                                
                ebone.head = child.global_transform @ Vector((0,0,0))
                ebone.tail = child.global_transform @ Vector((0,1,0))
                ebone.length = 0.01
                ebone.align_roll(child.global_transform @ Vector((0,0,1)) - ebone.head)
                if parent:
                    ebone.parent = parent.edit_bone

    return armature, obj
def import_texture(name : str, data : Texture2D):
    '''Imports Texture2D assets into blender

    Args:
        name (str): asset name
        data (Texture2D): source texture

    Returns:
        bpy.types.Image: Created image
    '''
    with tempfile.NamedTemporaryFile(suffix='.bmp',delete=False) as temp:
        print('* Saving Texture', name, 'to', temp.name)
        data.image.save(temp)
        temp.close()
        img = bpy.data.images.load(temp.name, check_existing=True)        
        img.name = name
        print('* Imported Texture', name)
        return img
def import_material(name : str,data : Material):
    '''Imports Material assets into blender. 
    A custom shader / material block is imported from the SekaiShaderStandalone.blend file.
    

    Args:
        name (str): material name
        data (Material): UnityPy Material

    Returns:
        bpy.types.Material: Created material        
    '''
    if not 'SekaiShader' in bpy.data.materials:
        print('! No SekaiShader found. Importing from source.')
        with bpy.data.libraries.load(SHADER_BLEND_FILE, link=False) as (data_from, data_to):
            data_to.materials = data_from.materials
            print('! Loaded shader blend file.')
    material = bpy.data.materials["SekaiShader"].copy()
    material.name = name
    def setup_texnode(ppTexture):
        texCoord = material.node_tree.nodes.new('ShaderNodeTexCoord')
        uvRemap = material.node_tree.nodes.new('ShaderNodeMapping')
        uvRemap.inputs[1].default_value[0] = ppTexture.m_Offset.X
        uvRemap.inputs[1].default_value[1] = ppTexture.m_Offset.Y
        uvRemap.inputs[3].default_value[0] = ppTexture.m_Scale.X
        uvRemap.inputs[3].default_value[1] = ppTexture.m_Scale.Y
        texture : Texture2D = ppTexture.m_Texture.read()
        texNode = material.node_tree.nodes.new('ShaderNodeTexImage')
        texNode.image = import_texture(texture.name, texture)
        material.node_tree.links.new(texCoord.outputs['UV'], uvRemap.inputs['Vector'])
        material.node_tree.links.new(uvRemap.outputs['Vector'], texNode.inputs['Vector'])
        return texNode
    sekaiShader = material.node_tree.nodes['Group']
    mainTex = setup_texnode(data.m_SavedProperties.m_TexEnvs['_MainTex'])
    shadowTex = setup_texnode(data.m_SavedProperties.m_TexEnvs['_ShadowTex'])
    valueTex = setup_texnode(data.m_SavedProperties.m_TexEnvs['_ValueTex'])
    material.node_tree.links.new(mainTex.outputs['Color'], sekaiShader.inputs[0])
    material.node_tree.links.new(shadowTex.outputs['Color'], sekaiShader.inputs[1])
    material.node_tree.links.new(valueTex.outputs['Color'], sekaiShader.inputs[2])
    return material

def check_is_object_sssekai_imported_armature(arm_obj):
    assert arm_obj and arm_obj.type == 'ARMATURE', "Please select an armature"
    mesh_obj = arm_obj.children[0]
    mesh = mesh_obj.data
    assert KEY_BONE_NAME_HASH_TBL in mesh or KEY_SHAPEKEY_NAME_HASH_TBL in mesh, "This armature is not imported by SSSekai."

def import_armature_animation(arm_obj : bpy.types.Object, name : str, data : Animation):
    mesh_obj = arm_obj.children[0]
    mesh = mesh_obj.data    
    print('* Importing Animation', name)
    bone_table = json.loads(mesh[KEY_BONE_NAME_HASH_TBL]) if KEY_BONE_NAME_HASH_TBL in mesh else dict()
    bpy.ops.object.mode_set(mode='EDIT')
    # Collect bone space <-> local space matrices
    local_space_trans_rot = dict() # i.e. parent space
    for bone in arm_obj.data.edit_bones: # Must be done in edit mode
        local_space_trans_rot[bone.name] = (Vector(bone[KEY_BINDPOSE_TRANS]), BlenderQuaternion(bone[KEY_BINDPOSE_QUAT]))
    # from glTF-Blender-IO:
    # ---
    # We have the final TRS of the bone in values. We need to give
    # the TRS of the pose bone though, which is relative to the edit
    # bone.
    #
    #     Final = EditBone * PoseBone
    #   where
    #     Final =    Trans[ft] Rot[fr] Scale[fs]
    #     EditBone = Trans[et] Rot[er]
    #     PoseBone = Trans[pt] Rot[pr] Scale[ps]
    #
    # Solving for PoseBone gives
    #
    #     pt = Rot[er^{-1}] (ft - et)
    #     pr = er^{-1} fr
    #     ps = fs 
    # ---
    def to_pose_quaternion(bone : bpy.types.PoseBone, quat : BlenderQuaternion):
        etrans, erot = local_space_trans_rot[bone.name]
        erot_inv = erot.conjugated()
        return erot_inv @ quat
    def to_pose_translation(bone : bpy.types.PoseBone, vec : Vector):
        etrans, erot = local_space_trans_rot[bone.name]
        erot_inv = erot.conjugated()
        return erot_inv @ (vec - etrans)
    def to_pose_euler(bone : bpy.types.PoseBone, euler : Euler):
        etrans, erot = local_space_trans_rot[bone.name]
        erot_inv = erot.conjugated()
        result = erot_inv @ euler.to_quaternion()
        result = result.to_euler('XYZ') # XXX make compatible for euler interpolation
        return result
    # Reset the pose 
    bpy.ops.object.mode_set(mode='POSE')
    bpy.ops.pose.select_all(action='SELECT')
    bpy.ops.pose.transforms_clear()
    bpy.ops.pose.select_all(action='DESELECT')    
    arm_obj.animation_data_clear()
    # Set the fps. Otherwise keys may get lost!
    bpy.context.scene.render.fps = int(data.Framerate)
    print('* Blender FPS:', bpy.context.scene.render.fps)
    def time_to_frame(time : float):
        return int(time * bpy.context.scene.render.fps) + 1
    arm_obj.animation_data_create()
    # Setup actions
    action = bpy.data.actions.new(name)
    arm_obj.animation_data.action = action
    for bone_hash, track in data.TransformTracks[TransformType.Rotation].items():
        # Quaternion rotations
        # TODO: Ensure minimum rotation path (i.e. negiboring quats dots > 0)
        bone_name = bone_table[str(bone_hash)]
        bone = arm_obj.pose.bones[bone_name]
        bone.rotation_mode = 'QUATERNION'
        fcurve_W = action.fcurves.new(data_path='pose.bones["%s"].rotation_quaternion' % bone_name,index=0)
        fcurve_X = action.fcurves.new(data_path='pose.bones["%s"].rotation_quaternion' % bone_name,index=1)
        fcurve_Y = action.fcurves.new(data_path='pose.bones["%s"].rotation_quaternion' % bone_name,index=2)
        fcurve_Z = action.fcurves.new(data_path='pose.bones["%s"].rotation_quaternion' % bone_name,index=3)
        for keyframe in track.Curve:
            frame = time_to_frame(keyframe.time)
            value = to_pose_quaternion(bone, swizzle_quaternion(keyframe.value))
            fcurve_W.keyframe_points.insert(frame, value.w)
            fcurve_X.keyframe_points.insert(frame, value.x)
            fcurve_Y.keyframe_points.insert(frame, value.y)
            fcurve_Z.keyframe_points.insert(frame, value.z)
    for bone_hash, track in data.TransformTracks[TransformType.EulerRotation].items():
        # Euler rotations
        bone_name = bone_table[str(bone_hash)]
        bone = arm_obj.pose.bones[bone_name]
        bone.rotation_mode = 'XYZ'
        fcurve_X = action.fcurves.new(data_path='pose.bones["%s"].rotation_euler' % bone_name,index=0)
        fcurve_Y = action.fcurves.new(data_path='pose.bones["%s"].rotation_euler' % bone_name,index=1)
        fcurve_Z = action.fcurves.new(data_path='pose.bones["%s"].rotation_euler' % bone_name,index=2)
        for keyframe in track.Curve:
            frame = time_to_frame(keyframe.time)
            value = to_pose_euler(bone, swizzle_euler(keyframe.value))
            fcurve_X.keyframe_points.insert(frame, value.x)
            fcurve_Y.keyframe_points.insert(frame, value.y)
            fcurve_Z.keyframe_points.insert(frame, value.z)  
    for bone_hash, track in data.TransformTracks[TransformType.Translation].items():
        # Translations
        bone_name = bone_table[str(bone_hash)]
        bone = arm_obj.pose.bones[bone_name]
        fcurve_X = action.fcurves.new(data_path='pose.bones["%s"].location' % bone_name,index=0)
        fcurve_Y = action.fcurves.new(data_path='pose.bones["%s"].location' % bone_name,index=1)
        fcurve_Z = action.fcurves.new(data_path='pose.bones["%s"].location' % bone_name,index=2)
        for keyframe in track.Curve:
            frame = time_to_frame(keyframe.time)
            value = to_pose_translation(bone, swizzle_vector(keyframe.value))
            fcurve_X.keyframe_points.insert(frame, value.x)
            fcurve_Y.keyframe_points.insert(frame, value.y)
            fcurve_Z.keyframe_points.insert(frame, value.z)            
    # Scale is ignored for now
    # ...
if BLENDER:
    class SSSekaiBlenderMeshImportOperator(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
        bl_idname = "sssekai.import_mesh"
        bl_label = "SSSekai Mesh Importer"
        filename_ext = "*.*"
        def execute(self, context):
            ab_file = open(self.filepath, 'rb')
            print('* Loading', self.filepath)
            env = load_assetbundle(ab_file)
            static_mesh_gameobjects, armatures = search_env_meshes(env)

            for mesh_go in static_mesh_gameobjects:
                mesh_rnd : MeshRenderer = mesh_go.m_MeshRenderer.read()
                mesh_data : Mesh = mesh_rnd.m_Mesh.read()    
                mesh, obj = import_mesh(mesh_go.name, mesh_data,False)    
                print('* Created Static Mesh', mesh_data.name)

            for armature in armatures:
                mesh_rnd : SkinnedMeshRenderer = armature.skinned_mesh_gameobject.m_SkinnedMeshRenderer.read()
                mesh_data : Mesh = mesh_rnd.m_Mesh.read()
                armInst, armObj = import_armature('%s_Armature' % armature.name ,armature)
                mesh, obj = import_mesh(armature.name, mesh_data,True, armature.bone_path_hash_tbl)
                obj.parent = armObj
                obj.modifiers.new('Armature', 'ARMATURE').object = armObj
                for ppmat in mesh_rnd.m_Materials:
                    material : Material = ppmat.read()
                    asset = import_material(material.name, material)
                    obj.data.materials.append(asset)
                    print('* Created Material', material.name)
                print('* Created Armature', armature.name, 'for Skinned Mesh', mesh_data.name)    
            
            return {'FINISHED'}    

if __name__ == "__main__":
    if BLENDER:
        bpy.utils.register_class(SSSekaiBlenderMeshImportOperator)
        def import_func(self, context):
            self.layout.operator(SSSekaiBlenderMeshImportOperator.bl_idname, text="SSSekai Mesh Importer")
        bpy.types.TOPBAR_MT_file_import.append(import_func)
    # ---- TESTING ----
    if BLENDER:
        arm_obj = bpy.context.active_object
        check_is_object_sssekai_imported_armature(arm_obj)    
    with open(r"F:\Sekai\live_pv\timeline\0001\character",'rb') as f:
        ab = load_assetbundle(f)
        animations = search_env_animations(ab)
        for animation in animations:
            print('* Found AnimationClip:', animation.name)
            if 'motion_0001' in animation.name:
                print('* Reading AnimationClip:', animation.name)
                print('* Byte size (compressed):',animation.byte_size)
                clip = read_animation(animation)  
                import_armature_animation(bpy.context.active_object, animation.name, clip)
                break
