# -----------------------------------------------------------------------------
# MIXAMO CHARACTER  <-  MANNEQUIN ANIMATIONS : one-shot retarget pipeline (v4.1)
#
# HOW TO USE: in the Content Browser, select your character's Skeletal Mesh
# (the one imported from the Mixamo-rigged FBX), then run this script.
# There is nothing to edit.
#
# What it does:
#   1. Finds the Mannequin (Manny/Quinn) automatically as the animation source.
#   2. Creates (or reuses) an IK Rig for the Mannequin and one for your character,
#      detecting which bones your character really has (fingers, toes...).
#   3. Creates (or reuses) the IK Retargeter, maps every chain, fixes the feet and sets the
#      "Root Motion" op: ON if the skeleton has a "root" bone, OFF if not (v4.1).
#   4. Retargets the Mannequin's ANIMATION BLUEPRINT (ABP_Unarmed) with everything it uses
#      (blendspaces, animations) into <your character folder>/Retarget/AnimBP. That gives you an
#      Anim Class to assign to a Character Blueprint.
#   5. Optionally retargets every loose Unarmed animation too (RETARGET_LOOSE_ANIMS).
#   6. Sanity-checks the result and prints a summary.
#   7. Makes a playable Character Blueprint: a COPY of the project's Third Person Blueprint with your mesh
#      and the new Anim Class (the original is never modified). Assets are named after the mesh without
#      its SK_ prefix (SK_Pitare_Pig -> ABP_Unarmed_Pitare_Pig, BP_Pitare_Pig_ThirdPerson).
#      Also a COPY of the template GameMode (BP_Pitare_Pig_GameMode) with that character as Default Pawn Class.
#
# It only CREATES new assets inside <your character folder>/Retarget.
# Every log line starts with "[PL]".
# -----------------------------------------------------------------------------
import math
import traceback

import unreal

# Optional tuning (you normally never touch these)
SRC_MESH_CANDIDATES = [
    "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple",
    "/Game/Characters/Mannequins/Meshes/SKM_Manny",
    "/Game/Characters/Mannequins/Meshes/SKM_Quinn_Simple",
    "/Game/Characters/Mannequins/Meshes/SKM_Quinn",
]
ANIM_SEARCH_ROOT = "/Game/Characters/Mannequins/Anims"
ANIM_FOLDER_FILTER = "/Unarmed"          # only animations whose folder contains this text; "" = all
ANIM_SKIP_CONTAINS = ["_Additive", "_AO_"]
SUFFIX = ""                              # "" -> animation names stay the same, in the character's own folder
BATCH_SIZE = 25

ABP_SEARCH_ROOT = "/Game/Characters"
ABP_NAME_CONTAINS = "Unarmed"            # which Animation Blueprint to bring over
RETARGET_LOOSE_ANIMS = False             # True: also copy every loose animation under ANIM_FOLDER_FILTER
AUTO_ALIGN_POSE = True                   # align the character retarget pose to the Mannequin (fixes bent elbows)

TAG = "[PL]"


def log(m):
    unreal.log(f"{TAG} {m}")


def warn(m):
    unreal.log_warning(f"{TAG} {m}")


def section(t):
    log("")
    log(f"===== {t} =====")


class Abort(Exception):
    pass


# ----------------------------------------------------------------- inputs ---
def pkg_of(obj):
    return obj.get_path_name().split(".")[0]


def get_target_mesh():
    section("TARGET CHARACTER")
    sel = [a for a in unreal.EditorUtilityLibrary.get_selected_assets() if isinstance(a, unreal.SkeletalMesh)]
    if not sel:
        raise Abort("Select your character's Skeletal Mesh in the Content Browser, then run the script again.")
    if len(sel) > 1:
        warn(f"{len(sel)} skeletal meshes selected; using the first: {sel[0].get_name()}")
    log(f"character mesh: {sel[0].get_path_name()}")
    return sel[0]


def get_source_mesh():
    for p in SRC_MESH_CANDIDATES:
        if unreal.EditorAssetLibrary.does_asset_exist(p):
            log(f"source mannequin: {p}")
            return unreal.load_asset(p)
    raise Abort("Could not find Manny/Quinn under /Game/Characters/Mannequins/Meshes.")


def mesh_bones(mesh):
    skel = mesh.get_editor_property("skeleton")
    pose = unreal.AnimPoseExtensions.get_reference_pose(skel)
    return {str(b) for b in unreal.AnimPoseExtensions.get_bone_names(pose)}


def detect_prefix(bones):
    for b in bones:
        if b.endswith("Hips"):
            return b[: -len("Hips")]
    raise Abort("No 'Hips' bone found: this does not look like a Mixamo skeleton.")


def chain_defs(bones, pfx):
    """List of (chain name, start bone, end bone) for bones that really exist."""
    raw = [("Spine", "Spine", "Spine2" if pfx + "Spine2" in bones else "Spine1"),
           ("Neck", "Neck", "Neck"), ("Head", "Head", "Head")]
    for s in ("Left", "Right"):
        raw += [
            (f"{s}Clavicle", f"{s}Shoulder", f"{s}Shoulder"),
            (f"{s}Arm", f"{s}Arm", f"{s}Hand"),
            (f"{s}Thumb", f"{s}HandThumb1", f"{s}HandThumb3"),
            (f"{s}Index", f"{s}HandIndex1", f"{s}HandIndex3"),
            (f"{s}Middle", f"{s}HandMiddle1", f"{s}HandMiddle3"),
            (f"{s}Ring", f"{s}HandRing1", f"{s}HandRing3"),
            (f"{s}Pinky", f"{s}HandPinky1", f"{s}HandPinky3"),
            (f"{s}Leg", f"{s}UpLeg", f"{s}Foot"),
            (f"{s}Ball", f"{s}ToeBase", f"{s}ToeBase"),
        ]
    out = []
    for name, a, b in raw:
        if pfx + a in bones and pfx + b in bones:
            out.append((name, pfx + a, pfx + b))
        else:
            log(f"skipping chain {name}: bone {a if pfx + a not in bones else b} not present")
    return out


# ----------------------------------------------------------------- assets ---
def create_or_load(folder, name, cls, factory):
    p = f"{folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(p):
        log(f"reusing existing {p}")
        return unreal.load_asset(p), False
    obj = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, cls, factory)
    if obj is None:
        raise Abort(f"create_asset returned None for {name}")
    log(f"created {p}")
    return obj, True


def build_source_rig(folder, name, src_mesh):
    rig, new = create_or_load(folder, name, unreal.IKRigDefinition, unreal.IKRigDefinitionFactory())
    if new:
        ctrl = unreal.IKRigController.get_controller(rig)
        ok1 = ctrl.set_skeletal_mesh(src_mesh)
        ok2 = ctrl.apply_auto_generated_retarget_definition()
        n = len(list(ctrl.get_retarget_chains()))
        log(f"set_skeletal_mesh={ok1}, auto chains={ok2}, {n} chain(s)")
        if not ok1 or n == 0:
            raise Abort("Source IK Rig has no chains")
    return rig


def build_target_rig(folder, name, tgt_mesh, chains, root):
    rig, new = create_or_load(folder, name, unreal.IKRigDefinition, unreal.IKRigDefinitionFactory())
    if new:
        ctrl = unreal.IKRigController.get_controller(rig)
        if not ctrl.set_skeletal_mesh(tgt_mesh):
            raise Abort("Target mesh rejected by IK Rig")
        log(f"set_retarget_root('{root}') -> {ctrl.set_retarget_root(root)}")
        for cname, start, end in chains:
            ctrl.add_retarget_chain(cname, start, end, "")
        log(f"{len(list(ctrl.get_retarget_chains()))} chain(s) created (expected {len(chains)})")
    return rig


def _offset_deg(rc, bone, tgt):
    for b in (unreal.Name(bone), bone):
        try:
            q = rc.get_rotation_offset_for_retarget_pose_bone(b, tgt)
            return f"{2 * math.degrees(math.acos(max(-1.0, min(1.0, abs(q.w))))):.1f}"
        except Exception:
            continue
    return "?"


def align_retarget_pose(rc, chains):
    """Same as the 'Auto Align' button of the IK Retargeter: rotates the TARGET's retarget pose so
    each limb points like the Mannequin's (fixes elbows/hands ending up bent the wrong way when the
    character's rest pose differs from Manny's).
    Spine/neck/head are put back to the character's own posture (Auto Align would straighten them and
    a hunched or arched character would lean in game). Auto Align depends on the current pose, so one
    pass is not enough: it is repeated until the limb angles stop changing (same as pressing the
    button again by hand)."""
    section("ALIGN RETARGET POSE (Auto Align, Chain to Chain)")
    tgt = unreal.RetargetSourceOrTarget.TARGET
    method = unreal.RetargetAutoAlignMethod.CHAIN_TO_CHAIN
    attempts = [("(TARGET, CHAIN_TO_CHAIN)", lambda: rc.auto_align_all_bones(tgt, method)),
                ("(CHAIN_TO_CHAIN, TARGET)", lambda: rc.auto_align_all_bones(method, tgt)),
                ("(TARGET)", lambda: rc.auto_align_all_bones(tgt))]
    pfx = next((c[1][:-len("Spine")] for c in chains if c[0] == "Spine"), "")
    keep = [pfx + b for b in ("Spine", "Spine1", "Spine2", "Neck", "Head")]
    watch = [pfx + b for b in ("LeftUpLeg", "RightUpLeg", "LeftArm", "RightArm", "LeftForeArm")]
    state = {"align": None}

    def align_once():
        order = [state["align"]] if state["align"] is not None else attempts
        for label, call in order:
            try:
                call()
                if state["align"] is None:
                    state["align"] = (label, call)
                    log(f"auto_align_all_bones{label}: accepted")
                return True
            except Exception as e:
                log(f"auto_align_all_bones{label} not accepted: {e!r}")
        return False

    def reset_posture():
        for pose_arg in (lambda: rc.get_current_retarget_pose_name(tgt), lambda: unreal.Name("Default Pose")):
            for names in ([unreal.Name(b) for b in keep], keep):
                try:
                    rc.reset_retarget_pose(pose_arg(), names, tgt)
                    return True
                except Exception as e:
                    log(f"reset_retarget_pose not accepted: {e!r}")
        return False

    def offsets(bones):
        return {b: _offset_deg(rc, b, tgt) for b in bones}

    def num(v):
        try:
            return float(v)
        except Exception:
            return None

    prev = None
    reset_ok = True
    for i in range(1, 6):
        if not align_once():
            warn("Could not auto-align the retarget pose: arms/hands may look off. "
                 "Manual fix: open the Retargeter, Edit Pose, Auto Align, Chain to Chain.")
            return
        before = offsets(keep)
        reset_ok = reset_posture()
        cur = offsets(watch)
        log(f"pass {i}: spine before reset " + ", ".join(f"{k.split(':')[-1]}={v}" for k, v in before.items() if k.endswith(('Spine', 'Spine1'))) +
            " | limbs " + ", ".join(f"{k.split(':')[-1]}={v}" for k, v in cur.items()))
        if prev is not None and all(num(cur[k]) is not None and num(prev[k]) is not None and
                                    abs(num(cur[k]) - num(prev[k])) < 0.5 for k in watch):
            log(f"pose converged after {i} passes")
            break
        prev = cur
    else:
        log("pose did not fully settle after 5 passes (using the last one)")
    if reset_ok:
        log("posture kept: spine/neck/head have the character's own pose. Offsets now: " +
            ", ".join(f"{k.split(':')[-1]}={v}" for k, v in offsets(keep).items()))
    else:
        warn("Could not reset spine/neck/head in the retarget pose: the character may lean forward/back. "
             "Manual fix: Retargeter > Edit Pose > select Spine..Head bones > Reset Selected.")


def find_root_bone(bones):
    """True if the skeleton has a dedicated 'root' bone (any case, optional prefix)."""
    return any(b.split(":")[-1].lower() == "root" for b in bones)


def build_retargeter(folder, name, src_rig, tgt_rig, chains, has_root=False):
    rtg, new = create_or_load(folder, name, unreal.IKRetargeter, unreal.IKRetargetFactory())
    rc = unreal.IKRetargeterController.get_controller(rtg)
    if new:
        rc.set_ik_rig(unreal.RetargetSourceOrTarget.SOURCE, src_rig)
        rc.set_ik_rig(unreal.RetargetSourceOrTarget.TARGET, tgt_rig)
        if rc.get_num_retarget_ops() == 0:
            rc.add_default_ops()
        try:
            rc.assign_ik_rig_to_all_ops(unreal.RetargetSourceOrTarget.TARGET, tgt_rig)
        except Exception as e:
            log(f"assign_ik_rig_to_all_ops skipped: {e!r}")
        rc.auto_map_chains(unreal.AutoMapChainType.FUZZY, True)

    # always (re)apply the fixes: they are idempotent
    names = {c[0] for c in chains}
    for target, source in {"LeftBall": "LeftFoot", "RightBall": "RightFoot"}.items():
        if target in names:
            log(f"forced mapping {target} <- {source}: {rc.set_source_chain(source, target)}")

    section("CHAIN MAPPING (target <- source)")
    bad = []
    for cname, _, _ in chains:
        src = str(rc.get_source_chain(cname))
        log(f"    {cname:14s} <- {src}")
        if src in ("None", ""):
            bad.append(cname)
    if bad:
        warn(f"UNMAPPED chains (those parts will not animate): {bad}")

    if AUTO_ALIGN_POSE:
        align_retarget_pose(rc, chains)

    # Root Motion op: ON when the skeleton has a real 'root' bone above Hips
    # (the pelvis keeps its own up/down motion). OFF when it has none (Manny's
    # root would otherwise be written into Hips and the body stretches).
    idx = rc.get_index_of_op_by_name("Root Motion")
    if idx >= 0:
        want = bool(has_root)
        rc.set_retarget_op_enabled(idx, want)
        state = rc.get_retarget_op_enabled(idx)
        log(f"skeleton has a 'root' bone: {has_root} -> Root Motion op enabled={state} (wanted {want})")
        if state != want:
            warn("Root Motion op could not be set as wanted: check the retargeter Op Stack.")
        if not has_root:
            warn("No 'root' bone in this skeleton: Root Motion op left OFF, the pelvis may look "
                 "stiff when walking. Add a 'root' bone above Hips (Blender) for best results.")
    else:
        warn("No 'Root Motion' op found.")
    return rtg


def save(folder, names):
    for n in names:
        log(f"save {folder}/{n} -> {unreal.EditorAssetLibrary.save_asset(f'{folder}/{n}', only_if_is_dirty=False)}")


# ------------------------------------------------------------- animations ---
def collect_anims():
    section("FIND ANIMATIONS")
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    found, dupes = {}, []
    for a in ar.get_assets_by_path(ANIM_SEARCH_ROOT, recursive=True):
        if str(a.asset_class_path.asset_name) != "AnimSequence":
            continue
        n = str(a.asset_name)
        if ANIM_FOLDER_FILTER and ANIM_FOLDER_FILTER not in str(a.package_path):
            continue
        if any(s in n for s in ANIM_SKIP_CONTAINS):
            continue
        if n in found:
            dupes.append(n)
            continue
        found[n] = a
    if dupes:
        warn(f"Skipped {len(set(dupes))} name(s) that appear in several folders: {sorted(set(dupes))[:10]}")
    log(f"{len(found)} animation(s) to retarget")
    return list(found.values())


def retarget(rtg, src_mesh, tgt_mesh, anims, out_anims):
    section("RETARGET")
    unreal.EditorAssetLibrary.make_directory(out_anims)
    created = []
    for i in range(0, len(anims), BATCH_SIZE):
        res = unreal.IKRetargetBatchOperation.duplicate_and_retarget(
            anims[i:i + BATCH_SIZE], src_mesh, tgt_mesh, rtg,
            search="", replace="", prefix="", suffix=SUFFIX,
            target_path=out_anims, use_source_path=False,
            include_referenced_assets=False, overwrite_existing_files=True,
        )
        for a in res:
            unreal.EditorAssetLibrary.save_asset(str(a.package_name), only_if_is_dirty=False)
            created.append(str(a.package_name))
        log(f"batch {i // BATCH_SIZE + 1}: {len(res)} asset(s) created")
    return created


def sanity_check(created, root):
    section("SANITY CHECK (Hips must stay at normal height)")
    if not created:
        warn("No animations were created.")
        return
    try:
        anim = unreal.load_asset(created[0])
        ref = unreal.AnimPoseExtensions.get_reference_pose(anim.get_editor_property("skeleton"))
        pose = unreal.AnimPoseExtensions.get_anim_pose_at_frame(anim, 0, unreal.AnimPoseEvaluationOptions())
        sp = unreal.AnimPoseSpaces.WORLD
        h_ref = unreal.AnimPoseExtensions.get_bone_pose(ref, root, sp).translation.z
        h_anim = unreal.AnimPoseExtensions.get_bone_pose(pose, root, sp).translation.z
        verdict = "OK" if abs(h_anim - h_ref) < 15 else "WARNING: Hips far from normal (body will stretch)"
        log(f"{created[0].split('/')[-1]}: Hips height anim={h_anim:.1f} cm, normal={h_ref:.1f} cm -> {verdict}")
    except Exception as e:
        warn(f"sanity check could not run: {e!r}")


# ------------------------------------------------------- animation blueprint ---
def retarget_anim_blueprint(rtg, src_mesh, tgt_mesh, folder, short):
    section("ANIMATION BLUEPRINT")
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    abps = [a for a in ar.get_assets_by_path(ABP_SEARCH_ROOT, recursive=True)
            if str(a.asset_class_path.asset_name) == "AnimBlueprint"
            and ABP_NAME_CONTAINS in str(a.asset_name) and "/Retarget" not in str(a.package_name)]
    if not abps:
        warn(f"No AnimBlueprint containing '{ABP_NAME_CONTAINS}' found under {ABP_SEARCH_ROOT}.")
        return None, []
    abp = abps[0]
    log(f"source Animation Blueprint: {abp.package_name}")
    try:
        opts = unreal.AssetRegistryDependencyOptions()
        opts.set_editor_property("include_hard_package_references", True)
        deps = sorted(str(d) for d in ar.get_dependencies(abp.package_name, opts) if str(d).startswith("/Game"))
        log(f"it directly depends on {len(deps)} asset(s) under /Game:")
        for d in deps:
            log(f"    {d}")
    except Exception as e:
        log(f"(dependency listing unavailable: {e!r})")

    out = f"{folder}/AnimBP"
    unreal.EditorAssetLibrary.make_directory(out)
    res = unreal.IKRetargetBatchOperation.duplicate_and_retarget(
        [abp], src_mesh, tgt_mesh, rtg,
        search="", replace="", prefix="", suffix=f"_{short}",
        target_path=out, use_source_path=False,
        include_referenced_assets=True, overwrite_existing_files=True,
    )
    log(f"{len(res)} asset(s) created in {out}:")
    new_abp, anims = None, []
    for a in res:
        cls = str(a.asset_class_path.asset_name)
        log(f"    {a.package_name}  ({cls})")
        unreal.EditorAssetLibrary.save_asset(str(a.package_name), only_if_is_dirty=False)
        if cls == "AnimBlueprint":
            new_abp = a
        elif cls == "AnimSequence":
            anims.append(str(a.package_name))

    section("COMPILE CHECK")
    abp_pkg = f"{out}/{abp.asset_name}_{short}"
    if new_abp is not None:
        abp_pkg = str(new_abp.package_name)
    if not unreal.EditorAssetLibrary.does_asset_exist(abp_pkg):
        warn(f"The new Animation Blueprint was not found at {abp_pkg}.")
        return None, anims
    log(f"new Animation Blueprint: {abp_pkg}")
    bp = unreal.load_asset(abp_pkg)
    try:
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        log("compile_blueprint called (red errors, if any, appear in the Output Log just above this line).")
    except Exception as e:
        warn(f"could not compile from Python: {e!r}")
    return abp_pkg, anims


# ------------------------------------------------------- character blueprint ---
TEMPLATE_BP_NAMES = ["BP_ThirdPersonCharacter"]   # templates a copy is made from (original never modified)
TEMPLATE_GAMEMODE_NAME = "BP_ThirdPersonGameMode"


def short_name(mesh_name):
    """Name used in the new assets: the mesh name without its SK_/SKM_ prefix (SK_Pitare_Pig -> Pitare_Pig)."""
    up = mesh_name.upper()
    for pre in ("SKM_", "SK_"):
        if up.startswith(pre) and len(mesh_name) > len(pre):
            return mesh_name[len(pre):]
    return mesh_name


def find_asset(name, class_name=None):
    ar = unreal.AssetRegistryHelpers.get_asset_registry()
    for a in ar.get_assets_by_path("/Game", recursive=True):
        if str(a.asset_name) == name and (class_name is None or str(a.asset_class_path.asset_name) == class_name):
            return a
    return None


def mesh_component_template(bp):
    """The SkeletalMeshComponent template ('Mesh') of a Blueprint."""
    sub = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    lib = unreal.SubobjectDataBlueprintFunctionLibrary
    for h in sub.k2_gather_subobject_data_for_blueprint(bp):
        obj = lib.get_object(lib.get_data(h))
        if obj is not None and isinstance(obj, unreal.SkeletalMeshComponent):
            return obj
    return None


def create_character_blueprint(tgt_mesh, abp_pkg, folder, short):
    """Playable third-person Character Blueprint: a COPY of the project's template with the new mesh + Anim Class."""
    section("CHARACTER BLUEPRINT")
    src = None
    for n in TEMPLATE_BP_NAMES:
        src = find_asset(n, "Blueprint")
        if src is not None:
            break
    if src is None:
        warn("No third-person Character Blueprint found in this project (looked for "
             f"{', '.join(TEMPLATE_BP_NAMES)}). The retarget is complete, but no playable Blueprint was made: "
             "a Character without the template's movement/camera input would not move. "
             "Create the project from the Third Person template (or copy that Blueprint in) and run again.")
        return None
    char_folder = pkg_of(tgt_mesh).rsplit("/", 1)[0]
    dst = f"{char_folder}/BP_{short}_ThirdPerson"
    if unreal.EditorAssetLibrary.does_asset_exist(dst):
        log(f"reusing existing {dst}")
    elif unreal.EditorAssetLibrary.duplicate_asset(str(src.package_name), dst):
        log(f"copied {src.package_name} -> {dst} (the original is untouched)")
    else:
        warn("Could not duplicate the template Blueprint.")
        return None
    bp = unreal.load_asset(dst)
    comp = mesh_component_template(bp)
    if comp is None:
        warn(f"Could not reach the Mesh component from Python. Open {dst} and set Skeletal Mesh Asset = "
             f"{tgt_mesh.get_name()} and Anim Class = {abp_pkg.rsplit('/', 1)[1]} by hand.")
        return dst
    comp.set_editor_property("skeletal_mesh_asset", tgt_mesh)
    comp.set_editor_property("anim_class", unreal.EditorAssetLibrary.load_blueprint_class(abp_pkg))
    log(f"Mesh component: mesh -> {tgt_mesh.get_name()}, anim class -> {abp_pkg.rsplit('/', 1)[1]}")
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    log(f"saved {dst}: {unreal.EditorAssetLibrary.save_asset(dst, only_if_is_dirty=False)}")
    return dst


def create_game_mode(char_bp_pkg, folder_of_mesh, short):
    """Copy of the template GameMode whose Default Pawn Class is the new character
    (World Settings -> GameMode Override takes a GameMode, not a character)."""
    src = find_asset(TEMPLATE_GAMEMODE_NAME, "Blueprint")
    if src is None:
        warn(f"{TEMPLATE_GAMEMODE_NAME} not found: no GameMode was made. In your own GameMode set "
             f"Default Pawn Class = {char_bp_pkg.rsplit('/', 1)[1]}.")
        return None
    dst = f"{folder_of_mesh}/BP_{short}_GameMode"
    if unreal.EditorAssetLibrary.does_asset_exist(dst):
        log(f"reusing existing {dst}")
    elif unreal.EditorAssetLibrary.duplicate_asset(str(src.package_name), dst):
        log(f"copied {src.package_name} -> {dst} (the original is untouched)")
    else:
        warn("Could not duplicate the template GameMode.")
        return None
    gm = unreal.load_asset(dst)
    char_class = unreal.EditorAssetLibrary.load_blueprint_class(char_bp_pkg)
    cdo = unreal.get_default_object(gm.generated_class())
    cdo.set_editor_property("default_pawn_class", char_class)
    unreal.BlueprintEditorLibrary.compile_blueprint(gm)
    got = cdo.get_editor_property("default_pawn_class")
    log(f"GameMode Default Pawn Class -> {got.get_name() if got else None}")
    log(f"saved {dst}: {unreal.EditorAssetLibrary.save_asset(dst, only_if_is_dirty=False)}")
    return dst


def main():
    log("Pipeline started.")
    try:
        tgt_mesh = get_target_mesh()
        src_mesh = get_source_mesh()
        base = tgt_mesh.get_name()
        folder = pkg_of(tgt_mesh).rsplit("/", 1)[0] + "/Retarget"
        unreal.EditorAssetLibrary.make_directory(folder)
        log(f"output folder: {folder}")

        bones = mesh_bones(tgt_mesh)
        pfx = detect_prefix(bones)
        chains = chain_defs(bones, pfx)
        root = pfx + "Hips"

        section("IK RIGS + RETARGETER")
        src_rig = build_source_rig(folder, "IK_Manny", src_mesh)
        tgt_rig = build_target_rig(folder, f"IK_{base}", tgt_mesh, chains, root)
        has_root = find_root_bone(bones)
        log(f"root bone present in target skeleton: {has_root}")
        rtg = build_retargeter(folder, f"RTG_Manny_to_{base}", src_rig, tgt_rig, chains, has_root)
        save(folder, ["IK_Manny", f"IK_{base}", f"RTG_Manny_to_{base}"])

        created = []
        if RETARGET_LOOSE_ANIMS:
            created += retarget(rtg, src_mesh, tgt_mesh, collect_anims(), f"{folder}/Anims")
        short = short_name(base)
        new_abp, abp_anims = retarget_anim_blueprint(rtg, src_mesh, tgt_mesh, folder, short)
        sanity_check(created + abp_anims, root)
        log("")
        if new_abp:
            bp_pkg = None
            try:
                bp_pkg = create_character_blueprint(tgt_mesh, new_abp, folder, short)
            except Exception:
                warn("Character Blueprint step failed (the retarget itself is fine):\n" + traceback.format_exc())
            log("")
            gm_pkg = None
            if bp_pkg:
                try:
                    gm_pkg = create_game_mode(bp_pkg, pkg_of(tgt_mesh).rsplit("/", 1)[0], short)
                except Exception:
                    warn("GameMode step failed (the Character Blueprint is fine):\n" + traceback.format_exc())
            log(f"DONE. Anim Class: {new_abp}")
            if bp_pkg:
                log(f"Playable character: {bp_pkg}")
            if gm_pkg:
                log(f"GameMode: {gm_pkg}")
                log("To try it: World Settings -> GameMode Override = that GameMode, then press Play.")
            elif bp_pkg:
                log("To try it: drag the Blueprint into the level and set Auto Possess Player = Player 0.")
        else:
            log("DONE (no Animation Blueprint was created, see warnings above).")
    except Abort as e:
        warn(f"STOPPED: {e}")
    except Exception:
        warn("UNEXPECTED ERROR:\n" + traceback.format_exc())

