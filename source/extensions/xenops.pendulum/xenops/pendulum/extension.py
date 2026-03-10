# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

import omni.ext
import omni.kit.commands
import omni.kit.menu.utils
import omni.kit
from omni.kit.menu.utils import MenuItemDescription
import omni.usd
from pxr import Usd, UsdGeom, UsdPhysics, UsdLux, Gf, Sdf
import omni.physx
from omni.physx import get_physx_interface
import omni.timeline
import carb.events
import math

print("[xenops.pendulum] Extension module imported")

class XenopsPendulumExtension(omni.ext.IExt):
    """Extension for creating a physics-based pendulum demonstration."""

    def on_startup(self, ext_id):
        """Called when the extension starts up."""
        print("[xenops.pendulum] Pendulum Demo Extension startup")

        self._timeline = omni.timeline.get_timeline_interface()
        self._events = omni.kit.app.get_app().get_message_bus_event_stream()

        self._baking = False

        # Add menu items
        self._menu_path = "Create/Physics/Pendulum Demo"
        omni.kit.menu.utils.add_menu_items(
            [
                MenuItemDescription(name="Pendulum Demo", onclick_fn=lambda: self.create_pendulum_scene()),
                MenuItemDescription(name="Start Bake", onclick_fn=lambda: self.start_bake()),
                MenuItemDescription(name="Stop Bake", onclick_fn=lambda: self.stop_bake()),
                MenuItemDescription(name="Save State", onclick_fn=lambda: self.save_state()),
                MenuItemDescription(name="Restore State", onclick_fn=lambda: self.restore_state()),
            ],
            "Create"
        )
        stage = omni.usd.get_context().get_stage()
        if stage and stage.GetPrimAtPath("/World/PendulumBob").IsValid():
            self._setup_rod_update(stage)

        print("[xenops.pendulum] Menu item added to Create > Physics")

    def on_shutdown(self):
        """Called when the extension shuts down."""
        print("[xenops.pendulum] Pendulum Demo Extension shutdown")

        if hasattr(self, '_physx_sub'):
            self._physx_sub.unsubscribe()
            self._physx_sub = None

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
        self._bob_prim = self._create_pendulum_bob(stage)
        self._create_pendulum_rod(stage, self._bob_prim)
        self._setup_pendulum_joint(stage)
        self._setup_rod_update(stage)

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
        # rod_xform.AddTranslateOp().Set(Gf.Vec3d(15, 75, 0))  # Midpoint

        # rotation = (0,90,0)
        # # Rotate to connect anchor and bob initially
        # rod_xform.AddRotateXYZOp().Set(Gf.Vec3f(*rotation))  # Initial angle
        # print(f"[xenops.pendulum] Initial rod rotation set to {rotation} degrees")
        # # Make rod kinematic (moves but doesn't have physics forces)
        # rigid_body = UsdPhysics.RigidBodyAPI.Apply(rod_cylinder.GetPrim())
        # rigid_body.CreateKinematicEnabledAttr().Set(True)

        self._rod_cylinder = rod_cylinder
        self._rod_translate_op = rod_xform.AddTranslateOp()
        self._rod_rotate_op = rod_xform.AddRotateXYZOp()

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

    def _setup_rod_update(self, stage):
        from omni.physx import get_physx_interface
        self._stage = stage
        self._anchor_pos = Gf.Vec3d(0, 100, 0)

        # Re-acquire handles from stage (survives reload)
        self._bob_prim = stage.GetPrimAtPath("/World/PendulumBob")
        bob_ops = UsdGeom.Xformable(self._bob_prim).GetOrderedXformOps()
        self._bob_translate_op = bob_ops[0]

        rod_prim = stage.GetPrimAtPath("/World/PendulumRod")
        self._rod_cylinder = UsdGeom.Cylinder(rod_prim)
        ops = UsdGeom.Xformable(rod_prim).GetOrderedXformOps()
        self._rod_translate_op = ops[0]
        self._rod_rotate_op = ops[1]

        self._physx_sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            lambda e: self._rotate_rod_to_bob(self._stage),
            name="pendulum_rod_update"
        )
        self._rotate_rod_to_bob(stage)

    def _on_physics_step(self, dt):
        self._rotate_rod_to_bob(self._stage)

    def save_state(self):
        """Save the bob's current world position and velocity as custom attributes on the prim."""
        xform_cache = UsdGeom.XformCache()
        bob_world = xform_cache.GetLocalToWorldTransform(self._bob_prim)
        pos = bob_world.ExtractTranslation()

        rigid_body = UsdPhysics.RigidBodyAPI(self._bob_prim)
        vel = rigid_body.GetVelocityAttr().Get() or Gf.Vec3f(0, 0, 0)

        self._bob_prim.CreateAttribute("pendulum:savedPosition", Sdf.ValueTypeNames.Vector3d, custom=True).Set(Gf.Vec3d(pos))
        self._bob_prim.CreateAttribute("pendulum:savedVelocity", Sdf.ValueTypeNames.Vector3f, custom=True).Set(Gf.Vec3f(vel))

        print(f"[xenops.pendulum] State saved — position: {pos}, velocity: {vel}")

    def restore_state(self):
        """Restore the bob's position and velocity from saved custom attributes."""
        pos_attr = self._bob_prim.GetAttribute("pendulum:savedPosition")
        vel_attr = self._bob_prim.GetAttribute("pendulum:savedVelocity")

        if not pos_attr.IsValid() or not vel_attr.IsValid():
            print("[xenops.pendulum] No saved state found — use Save State first")
            return

        pos = pos_attr.Get()
        vel = vel_attr.Get()

        bob_ops = UsdGeom.Xformable(self._bob_prim).GetOrderedXformOps()
        bob_ops[0].Set(Gf.Vec3d(pos))

        rigid_body = UsdPhysics.RigidBodyAPI(self._bob_prim)
        rigid_body.GetVelocityAttr().Set(Gf.Vec3f(vel))

        print(f"[xenops.pendulum] State restored — position: {pos}, velocity: {vel}")

    def start_bake(self):
        """Start recording time-sampled animation for the rod."""
        self._baking = True
        print("[xenops.pendulum] Baking started — rod transforms will be written as time samples")

    def stop_bake(self):
        """Stop recording and print a reminder to save the stage."""
        self._baking = False
        print("[xenops.pendulum] Baking stopped — save the stage to persist the recorded animation")

    def _rotate_rod_to_bob(self, stage):
        """Rotate the rod to always point towards the bob."""
        xform_cache = UsdGeom.XformCache()
        bob_world = xform_cache.GetLocalToWorldTransform(self._bob_prim)
        bob_pos = bob_world.ExtractTranslation()

        anchor = self._anchor_pos
        dx = bob_pos[0] - anchor[0]
        dy = bob_pos[1] - anchor[1]

        mid = Gf.Vec3d((anchor[0] + bob_pos[0]) / 2,
                    (anchor[1] + bob_pos[1]) / 2,
                    0)
        length = math.sqrt(dx * dx + dy * dy)
        angle = math.degrees(math.atan2(-dx, dy))

        if self._baking:
            frame = self._timeline.get_current_time() * self._timeline.get_time_codes_per_seconds()
            time = Usd.TimeCode(frame)
            self._bob_translate_op.Set(Gf.Vec3d(bob_pos), time)
            self._rod_translate_op.Set(mid, time)
            self._rod_rotate_op.Set(Gf.Vec3f(90, 0, angle), time)
            self._rod_cylinder.GetHeightAttr().Set(length, time)
        else:
            self._rod_translate_op.Set(mid)
            self._rod_rotate_op.Set(Gf.Vec3f(90, 0, angle))
            self._rod_cylinder.GetHeightAttr().Set(length)

    def _add_lighting(self, stage):
        """Add basic lighting to the scene."""
        # Create a distant light
        light_path = "/World/DistantLight"
        distant_light = UsdLux.DistantLight.Define(stage, light_path)
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
