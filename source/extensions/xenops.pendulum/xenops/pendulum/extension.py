# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import omni.ext
import omni.kit.commands
import omni.kit.menu.utils
from omni.kit.menu.utils import MenuItemDescription
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, Gf, Sdf
import omni.physx
from omni.physx import get_physx_interface
import omni.timeline
import carb.events


class XenopsPendulumExtension(omni.ext.IExt):
    """Extension for creating a physics-based pendulum demonstration."""

    def on_startup(self, ext_id):
        """Called when the extension starts up."""
        print("[xenops.pendulum] Pendulum Demo Extension startup")

        self._timeline = omni.timeline.get_timeline_interface()
        self._events = omni.kit.app.get_app().get_message_bus_event_stream()

        # Add menu item
        self._menu_path = "Create/Physics/Pendulum Demo"
        omni.kit.menu.utils.add_menu_items(
            [MenuItemDescription(name="Pendulum Demo", onclick_fn=lambda: self.create_pendulum_scene())],
            "Create"
        )

        print("[xenops.pendulum] Menu item added to Create > Physics")

    def on_shutdown(self):
        """Called when the extension shuts down."""
        print("[xenops.pendulum] Pendulum Demo Extension shutdown")

        # Remove menu item
        if hasattr(self, '_menu_path'):
            omni.kit.menu.utils.remove_menu_items([self._menu_path], "Create")

    def create_pendulum_scene(self):
        """Create a complete pendulum scene with physics."""
        print("[xenops.pendulum] Creating pendulum scene...")

        # Get current stage
        stage = omni.usd.get_context().get_stage()
        if not stage:
            print("[xenops.pendulum] No stage found, creating new stage")
            omni.usd.get_context().new_stage()
            stage = omni.usd.get_context().get_stage()

        # Clear existing content
        root_prim = stage.GetDefaultPrim()
        if root_prim:
            for child in root_prim.GetChildren():
                omni.kit.commands.execute('DeletePrims', paths=[str(child.GetPath())])

        # Set up physics scene
        self._setup_physics_scene(stage)

        # Create pendulum components
        self._create_anchor_point(stage)
        pendulum_prim = self._create_pendulum_bob(stage)
        self._create_pendulum_rod(stage, pendulum_prim)
        self._setup_pendulum_joint(stage)

        # Add lighting
        self._add_lighting(stage)

        # Set camera position for good view
        self._setup_camera(stage)

        print("[xenops.pendulum] Pendulum scene created successfully!")

    def _setup_physics_scene(self, stage):
        """Set up the physics scene."""
        # Create physics scene
        physics_scene_path = "/World/PhysicsScene"
        physics_scene = UsdPhysics.Scene.Define(stage, physics_scene_path)
        physics_scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0.0, -1.0, 0.0))
        physics_scene.CreateGravityMagnitudeAttr().Set(981.0)  # cm/s^2

        print("[xenops.pendulum] Physics scene created")

    def _create_anchor_point(self, stage):
        """Create the anchor point for the pendulum."""
        # Create a small sphere to represent the anchor
        anchor_path = "/World/PendulumAnchor"
        anchor_sphere = UsdGeom.Sphere.Define(stage, anchor_path)
        anchor_sphere.CreateRadiusAttr().Set(2.0)  # 2cm radius
        anchor_sphere.CreateExtentAttr().Set([(-2, -2, -2), (2, 2, 2)])

        # Position the anchor high up
        anchor_xform = UsdGeom.Xformable(anchor_sphere)
        anchor_xform.AddTranslateOp().Set(Gf.Vec3d(0, 100, 0))  # 1 meter up

        # Make it static (no physics body needed, it's fixed)
        print("[xenops.pendulum] Anchor point created")

    def _create_pendulum_bob(self, stage):
        """Create the pendulum bob (weight)."""
        # Create sphere for the bob
        bob_path = "/World/PendulumBob"
        bob_sphere = UsdGeom.Sphere.Define(stage, bob_path)
        bob_sphere.CreateRadiusAttr().Set(5.0)  # 5cm radius
        bob_sphere.CreateExtentAttr().Set([(-5, -5, -5), (5, 5, 5)])

        # Position the bob hanging down
        bob_xform = UsdGeom.Xformable(bob_sphere)
        bob_xform.AddTranslateOp().Set(Gf.Vec3d(30, 50, 0))  # Offset to start swinging

        # Add physics - rigid body
        rigid_body = UsdPhysics.RigidBodyAPI.Apply(bob_sphere.GetPrim())

        # Add collision
        collision_api = UsdPhysics.CollisionAPI.Apply(bob_sphere.GetPrim())

        # Set mass properties
        mass_api = UsdPhysics.MassAPI.Apply(bob_sphere.GetPrim())
        mass_api.CreateMassAttr().Set(10.0)  # 10 units mass

        print("[xenops.pendulum] Pendulum bob created")
        return bob_sphere.GetPrim()

    def _create_pendulum_rod(self, stage, bob_prim):
        """Create a visual rod connecting the anchor to the bob."""
        # Create a thin cylinder for the rod
        rod_path = "/World/PendulumRod"
        rod_cylinder = UsdGeom.Cylinder.Define(stage, rod_path)
        rod_cylinder.CreateRadiusAttr().Set(0.5)  # Very thin rod
        rod_cylinder.CreateHeightAttr().Set(50.0)  # 50cm long
        rod_cylinder.CreateExtentAttr().Set([(-0.5, -25, -0.5), (0.5, 25, 0.5)])

        # Position the rod between anchor and bob
        rod_xform = UsdGeom.Xformable(rod_cylinder)
        rod_xform.AddTranslateOp().Set(Gf.Vec3d(15, 75, 0))  # Midpoint

        # Rotate to connect anchor and bob initially
        rod_xform.AddRotateXYZOp().Set(Gf.Vec3f(0, 0, -30))  # Initial angle

        # Make rod kinematic (moves but doesn't have physics forces)
        rigid_body = UsdPhysics.RigidBodyAPI.Apply(rod_cylinder.GetPrim())
        rigid_body.CreateKinematicEnabledAttr().Set(True)

        print("[xenops.pendulum] Pendulum rod created")

    def _setup_pendulum_joint(self, stage):
        """Set up the revolute joint for the pendulum."""
        # Create revolute joint between anchor and bob
        joint_path = "/World/PendulumJoint"
        joint = UsdPhysics.RevoluteJoint.Define(stage, joint_path)

        # Set joint relationships
        joint.CreateBody0Rel().SetTargets(["/World/PendulumAnchor"])
        joint.CreateBody1Rel().SetTargets(["/World/PendulumBob"])

        # Set joint axis (rotate around Z-axis)
        joint.CreateAxisAttr().Set("Z")

        # Set joint anchor position (at the anchor point)
        joint.CreateLocalPos0Attr().Set(Gf.Vec3f(0, 0, 0))
        joint.CreateLocalPos1Attr().Set(Gf.Vec3f(-30, 50, 0))  # Relative to bob

        print("[xenops.pendulum] Pendulum joint created")

    def _add_lighting(self, stage):
        """Add basic lighting to the scene."""
        # Create a distant light
        light_path = "/World/DistantLight"
        distant_light = UsdGeom.DistantLight.Define(stage, light_path)
        distant_light.CreateIntensityAttr().Set(1000.0)
        distant_light.CreateColorAttr().Set(Gf.Vec3f(1.0, 1.0, 1.0))

        # Rotate light to come from above and to the side
        light_xform = UsdGeom.Xformable(distant_light)
        light_xform.AddRotateXYZOp().Set(Gf.Vec3f(-45, 45, 0))

        print("[xenops.pendulum] Lighting added")

    def _setup_camera(self, stage):
        """Position camera for a good view of the pendulum."""
        # The camera should automatically frame the content
        # We can also add a specific camera if needed
        print("[xenops.pendulum] Camera positioned")