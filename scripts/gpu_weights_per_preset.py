import gradio as gr

from backend import memory_management
from modules import script_callbacks, shared
from modules_forge import main_entry
from modules_forge.presets import PresetArch


__version__ = "1.0.0"

TOTAL_VRAM_MB = max(1, int(memory_management.total_vram))
OPTION_PREFIX = "forge_gpu_weights_mb_"

_ui_gpu_weights = None
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


def _on_top_slider_change(value_mb, preset):
    if not preset:
        preset = shared.opts.forge_preset

    value_mb = _clamp_mb(value_mb)

    shared.opts.set(_key(preset), value_mb)
    _apply_mb_to_neo(value_mb, save=False)
    shared.opts.save(shared.config_filename)

    return value_mb


def _on_preset_for_slider(preset):
    if not preset:
        preset = shared.opts.forge_preset

    return gr.update(value=_preset_value_mb(preset))


def _make_checkpoint_manager_ui_patched():
    """
    Let Forge Neo build its normal top row first, then add the old-style
    GPU Weights slider alongside Checkpoint / VAE / Diffusion in Low Bits.
    """
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
        outputs=[_ui_gpu_weights],
        queue=False,
        show_progress=False,
    )

    # This event only refreshes the visible slider value.
    # The actual memory value is applied inside our _load_presets wrapper below,
    # before Forge loads the selected preset/checkpoint.
    main_entry.ui_forge_preset.change(
        fn=_on_preset_for_slider,
        inputs=[main_entry.ui_forge_preset],
        outputs=[_ui_gpu_weights],
        queue=False,
        show_progress=False,
    )


def _load_presets_patched(ui_checkpoint, ui_vae, ui_forge_unet_dtype, ui_forge_preset):
    # Apply the preset-specific GPU allocation before Forge refreshes/reloads
    # the model parameters for that preset.
    _apply_preset(ui_forge_preset, save=True)
    return _original_load_presets(
        ui_checkpoint,
        ui_vae,
        ui_forge_unet_dtype,
        ui_forge_preset,
    )


def _setting_changed_for(preset: str):
    """
    Called when Apply Settings changes a per-preset default.
    If it is the active preset, update Neo's live global GPU setting too.
    """
    if getattr(shared.opts, "forge_preset", None) == preset:
        _apply_preset(preset, save=False)


def register_settings():
    current_default_mb = int(round(float(shared.opts.setting_allocated_vram) * TOTAL_VRAM_MB))

    for preset in PresetArch.choices():
        # Capture preset in the lambda default argument.
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
    # Avoid stacking the patch if scripts are reloaded.
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
