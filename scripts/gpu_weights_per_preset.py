import threading

import gradio as gr

from backend import memory_management
from modules import script_callbacks, shared
from modules_forge import main_entry
from modules_forge.presets import PresetArch


__version__ = "1.0.1"

TOTAL_VRAM_MB = max(1, int(memory_management.total_vram))
OPTION_PREFIX = "forge_gpu_weights_mb_"

_ui_gpu_weights = None

# Keep edits in memory while the user is dragging/typing.  Sending a value
# back into the same slider on every input caused delayed Gradio replies to
# overwrite the next mouse movement or digit.
_pending_values = {}
_pending_timer = None
_pending_lock = threading.RLock()
_SAVE_IDLE_SECONDS = 0.45

if not hasattr(main_entry, "_gpu_weights_per_preset_original_make_checkpoint_manager_ui"):
    main_entry._gpu_weights_per_preset_original_make_checkpoint_manager_ui = main_entry.make_checkpoint_manager_ui

if not hasattr(main_entry, "_gpu_weights_per_preset_original_load_presets"):
    main_entry._gpu_weights_per_preset_original_load_presets = main_entry._load_presets

_original_make_checkpoint_manager_ui = main_entry._gpu_weights_per_preset_original_make_checkpoint_manager_ui
_original_load_presets = main_entry._gpu_weights_per_preset_original_load_presets


def _key(preset: str) -> str:
    return f"{OPTION_PREFIX}{preset}"


def _clamp_mb(value) -> int:
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError):
        value = int(round(float(shared.opts.setting_allocated_vram) * TOTAL_VRAM_MB))
    return max(0, min(TOTAL_VRAM_MB, value))


def _preset_value_mb(preset: str) -> int:
    with _pending_lock:
        pending = _pending_values.get(preset)
    if pending is not None:
        return pending
    fallback = int(round(float(shared.opts.setting_allocated_vram) * TOTAL_VRAM_MB))
    return _clamp_mb(getattr(shared.opts, _key(preset), fallback))


def _apply_mb_to_neo(value_mb: int, *, save: bool = True):
    """Neo internally stores GPU Weights as a 0..1 fraction of total VRAM."""
    value_mb = _clamp_mb(value_mb)
    ratio = value_mb / TOTAL_VRAM_MB

    shared.opts.set("setting_allocated_vram", ratio)

    if save:
        shared.opts.save(shared.config_filename)


def _apply_preset(preset: str, *, save: bool = True) -> int:
    value_mb = _preset_value_mb(preset)
    _apply_mb_to_neo(value_mb, save=save)
    return value_mb


def _flush_pending():
    """Commit the latest user values; called after idle, mouse release or preset switch."""
    global _pending_timer
    with _pending_lock:
        if _pending_timer is not None:
            _pending_timer.cancel()
            _pending_timer = None
        if not _pending_values:
            return

        updates = dict(_pending_values)
        _pending_values.clear()
        for preset, value_mb in updates.items():
            shared.opts.set(_key(preset), value_mb)

        active_preset = getattr(shared.opts, "forge_preset", None)
        if active_preset in updates:
            _apply_mb_to_neo(updates[active_preset], save=False)
        shared.opts.save(shared.config_filename)


def _on_top_slider_change(value_mb, preset):
    """Stage only: no disk writes and no updates sent back into the active slider."""
    global _pending_timer
    if not preset:
        preset = shared.opts.forge_preset

    with _pending_lock:
        _pending_values[preset] = _clamp_mb(value_mb)
        if _pending_timer is not None:
            _pending_timer.cancel()
        _pending_timer = threading.Timer(_SAVE_IDLE_SECONDS, _flush_pending)
        _pending_timer.daemon = True
        _pending_timer.start()


def _on_top_slider_release(value_mb, preset):
    # Mouse release saves immediately. Manual number entry is covered by the
    # debounced .input() handler, which also handles blur/Enter edits.
    _on_top_slider_change(value_mb, preset)
    _flush_pending()


def _on_preset_for_slider(preset):
    if not preset:
        preset = shared.opts.forge_preset

    return gr.update(value=_preset_value_mb(preset))


def _make_checkpoint_manager_ui_patched():
    """Add the old-style GPU Weights slider alongside Forge's model controls."""
    global _ui_gpu_weights

    _original_make_checkpoint_manager_ui()

    preset = getattr(shared.opts, "forge_preset", "sd")
    current_mb = _apply_preset(preset, save=False)

    _ui_gpu_weights = gr.Slider(
        label="GPU Weights (MB)",
        value=current_mb,
        minimum=0,
        maximum=TOTAL_VRAM_MB,
        step=1,
        elem_id="forge_gpu_weights_per_preset",
    )

    _ui_gpu_weights.input(
        fn=_on_top_slider_change,
        inputs=[_ui_gpu_weights, main_entry.ui_forge_preset],
        outputs=[],
        queue=False,
        show_progress=False,
    )
    _ui_gpu_weights.release(
        fn=_on_top_slider_release,
        inputs=[_ui_gpu_weights, main_entry.ui_forge_preset],
        outputs=[],
        queue=False,
        show_progress=False,
    )

    # Only an actual preset switch should update the visible slider value.
    main_entry.ui_forge_preset.change(
        fn=_on_preset_for_slider,
        inputs=[main_entry.ui_forge_preset],
        outputs=[_ui_gpu_weights],
        queue=False,
        show_progress=False,
    )


def _load_presets_patched(ui_checkpoint, ui_vae, ui_forge_unet_dtype, ui_forge_preset):
    # Persist a pending edit before the model/preset switch. The active preset
    # is then selected by Forge in its normal order, as in previous versions.
    _flush_pending()
    _apply_preset(ui_forge_preset, save=True)
    return _original_load_presets(
        ui_checkpoint,
        ui_vae,
        ui_forge_unet_dtype,
        ui_forge_preset,
    )


def _setting_changed_for(preset: str):
    """Keep the active global allocation in sync when Settings are applied."""
    if getattr(shared.opts, "forge_preset", None) == preset:
        _apply_preset(preset, save=False)


def register_settings():
    current_default_mb = int(round(float(shared.opts.setting_allocated_vram) * TOTAL_VRAM_MB))

    for preset in PresetArch.choices():
        info = shared.OptionInfo(
            current_default_mb,
            f"{preset.upper()} GPU Weights (MB)",
            gr.Slider,
            {
                "minimum": 0,
                "maximum": TOTAL_VRAM_MB,
                "step": 1,
            },
            onchange=lambda preset=preset: _setting_changed_for(preset),
            section=("gpu_weights_per_preset", "GPU Weights per Preset"),
            category_id="ui",
        ).info("default used when this UI preset is selected")

        shared.opts.add_option(_key(preset), info)


def install_patch():
    if getattr(main_entry.make_checkpoint_manager_ui, "_gpu_weights_per_preset_patch", False):
        return

    _make_checkpoint_manager_ui_patched._gpu_weights_per_preset_patch = True
    main_entry.make_checkpoint_manager_ui = _make_checkpoint_manager_ui_patched
    main_entry._load_presets = _load_presets_patched

    print(
        f"[Forge Neo] GPU Weights per Preset v{__version__} loaded "
        f"(VRAM detected: {TOTAL_VRAM_MB} MB)"
    )


script_callbacks.on_ui_settings(register_settings)
install_patch()
