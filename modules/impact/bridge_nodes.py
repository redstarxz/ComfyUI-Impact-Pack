import os
from PIL import ImageOps
from impact.utils import *
import latent_preview


# NOTE: this should not be `from . import core`.
# I don't know why but... 'from .' and 'from impact' refer to different core modules.
# This separates global variables of the core module and breaks the preview bridge.
from impact import core
# <--
import random


class PreviewBridge:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
                    "images": ("IMAGE",),
                    "image": ("STRING", {"default": ""}),
                    },
                "optional": {
                    "block": ("BOOLEAN", {"default": False, "label_on": "if_empty_mask", "label_off": "never", "tooltip": "is_empty_mask: If the mask is empty, the execution is stopped.\nnever: The execution is never stopped."}),
                    "restore_mask": (["never", "always", "if_same_size"], {"tooltip": "if_same_size: If the changed input image is the same size as the previous image, restore using the last saved mask\nalways: Whenever the input image changes, always restore using the last saved mask\nnever: Do not restore the mask.\n`restore_mask` has higher priority than `block`"}),
                    },
                "hidden": {"unique_id": "UNIQUE_ID", "extra_pnginfo": "EXTRA_PNGINFO"},
                }

    RETURN_TYPES = ("IMAGE", "MASK", )

    FUNCTION = "doit"

    OUTPUT_NODE = True

    CATEGORY = "ImpactPack/Util"

    DESCRIPTION = "This is a feature that allows you to edit and send a Mask over a image.\nIf the block is set to 'is_empty_mask', the execution is stopped when the mask is empty."

    def __init__(self):
        super().__init__()
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prev_hash = None

    @staticmethod
    def load_image(pb_id):
        is_fail = False
        if pb_id not in core.preview_bridge_image_id_map:
            is_fail = True

        image_path, ui_item = core.preview_bridge_image_id_map[pb_id]

        if not os.path.isfile(image_path):
            is_fail = True

        if not is_fail:
            i = Image.open(image_path)
            i = ImageOps.exif_transpose(i)
            image = i.convert("RGB")
            image = np.array(image).astype(np.float32) / 255.0
            image = torch.from_numpy(image)[None,]

            if 'A' in i.getbands():
                mask = np.array(i.getchannel('A')).astype(np.float32) / 255.0
                mask = 1. - torch.from_numpy(mask)
            else:
                mask = torch.zeros((64, 64), dtype=torch.float32, device="cpu")
        else:
            image = empty_pil_tensor()
            mask = torch.zeros((64, 64), dtype=torch.float32, device="cpu")
            ui_item = {
                "filename": 'empty.png',
                "subfolder": '',
                "type": 'temp'
            }

        return image, mask.unsqueeze(0), ui_item

    def doit(self, images, image, unique_id, block=False, restore_mask="never", prompt=None, extra_pnginfo=None):
        need_refresh = False

        if unique_id not in core.preview_bridge_cache:
            need_refresh = True

        elif core.preview_bridge_cache[unique_id][0] is not images:
            need_refresh = True

        if not need_refresh:
            pixels, mask, path_item = PreviewBridge.load_image(image)
            image = [path_item]
        else:
            if restore_mask != "never":
                mask = core.preview_bridge_last_mask_cache.get(unique_id)
                if mask is None or (restore_mask != "always" and mask.shape[1:] != images.shape[1:3]):
                    mask = None
            else:
                mask = None

            if mask is None:
                mask = torch.zeros((64, 64), dtype=torch.float32, device="cpu")
                res = nodes.PreviewImage().save_images(images, filename_prefix="PreviewBridge/PB-", prompt=prompt, extra_pnginfo=extra_pnginfo)
            else:
                masked_images = tensor_convert_rgba(images)
                resized_mask = resize_mask(mask, (images.shape[1], images.shape[2])).unsqueeze(3)
                resized_mask = 1 - resized_mask
                tensor_putalpha(masked_images, resized_mask)
                res = nodes.PreviewImage().save_images(masked_images, filename_prefix="PreviewBridge/PB-", prompt=prompt, extra_pnginfo=extra_pnginfo)

            image2 = res['ui']['images']
            pixels = images

            path = os.path.join(folder_paths.get_temp_directory(), 'PreviewBridge', image2[0]['filename'])
            core.set_previewbridge_image(unique_id, path, image2[0])
            core.preview_bridge_image_id_map[image] = (path, image2[0])
            core.preview_bridge_image_name_map[unique_id, path] = (image, image2[0])
            core.preview_bridge_cache[unique_id] = (images, image2)

            image = image2

        is_empty_mask = torch.all(mask == 0)

        if block and is_empty_mask and core.is_execution_model_version_supported():
            from comfy_execution.graph import ExecutionBlocker
            result = ExecutionBlocker(None), ExecutionBlocker(None)
        elif block and is_empty_mask:
            print(f"[Impact Pack] PreviewBridge: ComfyUI is outdated - blocking feature is disabled.")
            result = pixels, mask
        else:
            result = pixels, mask

        if not is_empty_mask:
            core.preview_bridge_last_mask_cache[unique_id] = mask

        return {
            "ui": {"images": image},
            "result": result,
        }


def decode_latent(latent, preview_method, vae_opt=None):
    if vae_opt is not None:
        image = nodes.VAEDecode().decode(vae_opt, latent)[0]
        return image

    from comfy.cli_args import LatentPreviewMethod
    import comfy.latent_formats as latent_formats

    if preview_method.startswith("TAE"):
        decoder_name = None

        if preview_method == "TAESD15":
            decoder_name = "taesd"
        elif preview_method == 'TAESDXL':
            decoder_name = "taesdxl"
        elif preview_method == 'TAESD3':
            decoder_name = "taesd3"
        elif preview_method == 'TAEF1':
            decoder_name = "taef1"

        if decoder_name:
            vae = nodes.VAELoader().load_vae(decoder_name)[0]
            image = nodes.VAEDecode().decode(vae, latent)[0]
            return image

    if preview_method == "Latent2RGB-SD15":
        latent_format = latent_formats.SD15()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-SDXL":
        latent_format = latent_formats.SDXL()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-SD3":
        latent_format = latent_formats.SD3()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-SD-X4":
        latent_format = latent_formats.SD_X4()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-Playground-2.5":
        latent_format = latent_formats.SDXL_Playground_2_5()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-SC-Prior":
        latent_format = latent_formats.SC_Prior()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-SC-B":
        latent_format = latent_formats.SC_B()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-FLUX.1":
        latent_format = latent_formats.Flux()
        method = LatentPreviewMethod.Latent2RGB
    elif preview_method == "Latent2RGB-LTXV":
        latent_format = latent_formats.LTXV()
        method = LatentPreviewMethod.Latent2RGB
    else:
        print(f"[Impact Pack] PreviewBridgeLatent: '{preview_method}' is unsupported preview method.")
        latent_format = latent_formats.SD15()
        method = LatentPreviewMethod.Latent2RGB

    previewer = core.get_previewer("cpu", latent_format=latent_format, force=True, method=method)
    samples = latent_format.process_in(latent['samples'])

    pil_image = previewer.decode_latent_to_preview(samples)
    pixels_size = pil_image.size[0]*8, pil_image.size[1]*8
    resized_image = pil_image.resize(pixels_size, resample=LANCZOS)

    return to_tensor(resized_image).unsqueeze(0)


class PreviewBridgeLatent:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {
                    "latent": ("LATENT",),
                    "image": ("STRING", {"default": ""}),
                    "preview_method": (["Latent2RGB-FLUX.1",
                                        "Latent2RGB-SDXL", "Latent2RGB-SD15", "Latent2RGB-SD3",
                                        "Latent2RGB-SD-X4", "Latent2RGB-Playground-2.5",
                                        "Latent2RGB-SC-Prior", "Latent2RGB-SC-B",
                                        "Latent2RGB-LTXV",
                                        "TAEF1", "TAESDXL", "TAESD15", "TAESD3"],),
                    },
                "optional": {
                    "vae_opt": ("VAE", ),
                    "block": ("BOOLEAN", {"default": False, "label_on": "if_empty_mask", "label_off": "never", "tooltip": "is_empty_mask: If the mask is empty, the execution is stopped.\nnever: The execution is never stopped. Instead, it returns a white mask."}),
                    "restore_mask": (["never", "always", "if_same_size"], {"tooltip": "if_same_size: If the changed input latent is the same size as the previous latent, restore using the last saved mask\nalways: Whenever the input latent changes, always restore using the last saved mask\nnever: Do not restore the mask.\n`restore_mask` has higher priority than `block`\nIf the input latent already has a mask, do not restore mask."}),
                },
                "hidden": {"unique_id": "UNIQUE_ID", "prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
                }

    RETURN_TYPES = ("LATENT", "MASK", )

    FUNCTION = "doit"

    OUTPUT_NODE = True

    CATEGORY = "ImpactPack/Util"

    DESCRIPTION = "This is a feature that allows you to edit and send a Mask over a latent image.\nIf the block is set to 'is_empty_mask', the execution is stopped when the mask is empty."

    def __init__(self):
        super().__init__()
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prev_hash = None
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))

    @staticmethod
    def load_image(pb_id):
        is_fail = False
        if pb_id not in core.preview_bridge_image_id_map:
            is_fail = True

        image_path, ui_item = core.preview_bridge_image_id_map[pb_id]

        if not os.path.isfile(image_path):
            is_fail = True

        if not is_fail:
            i = Image.open(image_path)
            i = ImageOps.exif_transpose(i)
            image = i.convert("RGB")
            image = np.array(image).astype(np.float32) / 255.0
            image = torch.from_numpy(image)[None,]

            if 'A' in i.getbands():
                mask = np.array(i.getchannel('A')).astype(np.float32) / 255.0
                mask = 1. - torch.from_numpy(mask)
            else:
                mask = None
        else:
            image = empty_pil_tensor()
            mask = None
            ui_item = {
                "filename": 'empty.png',
                "subfolder": '',
                "type": 'temp'
            }

        return image, mask, ui_item

    def doit(self, latent, image, preview_method, vae_opt=None, block=False, unique_id=None, restore_mask='never', prompt=None, extra_pnginfo=None):
        latent_channels = latent['samples'].shape[1]

        if 'SD3' in preview_method or 'SC-Prior' in preview_method or 'FLUX.1' in preview_method or 'TAEF1' == preview_method:
            preview_method_channels = 16
        elif 'LTXV' in preview_method:
            preview_method_channels = 128
        else:
            preview_method_channels = 4

        if vae_opt is None and latent_channels != preview_method_channels:
            print(f"[PreviewBridgeLatent] The version of latent is not compatible with preview_method.\nSD3, SD1/SD2, SDXL, SC-Prior, SC-B and FLUX.1 are not compatible with each other.")
            raise Exception("The version of latent is not compatible with preview_method.<BR>SD3, SD1/SD2, SDXL, SC-Prior, SC-B and FLUX.1 are not compatible with each other.")

        need_refresh = False

        if unique_id not in core.preview_bridge_cache:
            need_refresh = True

        elif (core.preview_bridge_cache[unique_id][0] is not latent
              or (vae_opt is None and core.preview_bridge_cache[unique_id][2] is not None)
              or (vae_opt is None and core.preview_bridge_cache[unique_id][1] != preview_method)
              or (vae_opt is not None and core.preview_bridge_cache[unique_id][2] is not vae_opt)):
            need_refresh = True

        if not need_refresh:
            pixels, mask, path_item = PreviewBridge.load_image(image)

            if mask is None:
                mask = torch.ones(latent['samples'].shape[2:], dtype=torch.float32, device="cpu").unsqueeze(0)
                if 'noise_mask' in latent:
                    res_latent = latent.copy()
                    del res_latent['noise_mask']
                else:
                    res_latent = latent

                is_empty_mask = True
            else:
                res_latent = latent.copy()
                res_latent['noise_mask'] = mask

                is_empty_mask = torch.all(mask == 1)

            res_image = [path_item]
        else:
            decoded_image = decode_latent(latent, preview_method, vae_opt)

            if 'noise_mask' in latent:
                mask = latent['noise_mask'].squeeze(0)  # 4D mask -> 3D mask

                decoded_pil = to_pil(decoded_image)

                inverted_mask = 1 - mask  # invert
                resized_mask = resize_mask(inverted_mask, (decoded_image.shape[1], decoded_image.shape[2]))
                result_pil = apply_mask_alpha_to_pil(decoded_pil, resized_mask)

                full_output_folder, filename, counter, _, _ = folder_paths.get_save_image_path("PreviewBridge/PBL-"+self.prefix_append, folder_paths.get_temp_directory(), result_pil.size[0], result_pil.size[1])
                file = f"{filename}_{counter}.png"
                result_pil.save(os.path.join(full_output_folder, file), compress_level=4)
                res_image = [{
                                'filename': file,
                                'subfolder': 'PreviewBridge',
                                'type': 'temp',
                            }]

                is_empty_mask = False
            else:
                if restore_mask != "never":
                    mask = core.preview_bridge_last_mask_cache.get(unique_id)
                    if mask is None or (restore_mask != "always" and mask.shape[1:] != decoded_image.shape[1:3]):
                        mask = None
                else:
                    mask = None

                if mask is None:
                    mask = torch.ones(latent['samples'].shape[2:], dtype=torch.float32, device="cpu").unsqueeze(0)
                    res = nodes.PreviewImage().save_images(decoded_image, filename_prefix="PreviewBridge/PBL-", prompt=prompt, extra_pnginfo=extra_pnginfo)
                else:
                    masked_images = tensor_convert_rgba(decoded_image)
                    resized_mask = resize_mask(mask, (decoded_image.shape[1], decoded_image.shape[2])).unsqueeze(3)
                    resized_mask = 1 - resized_mask
                    tensor_putalpha(masked_images, resized_mask)
                    res = nodes.PreviewImage().save_images(masked_images, filename_prefix="PreviewBridge/PBL-", prompt=prompt, extra_pnginfo=extra_pnginfo)

                res_image = res['ui']['images']

            is_empty_mask = torch.all(mask == 1)

            path = os.path.join(folder_paths.get_temp_directory(), 'PreviewBridge', res_image[0]['filename'])
            core.set_previewbridge_image(unique_id, path, res_image[0])
            core.preview_bridge_image_id_map[image] = (path, res_image[0])
            core.preview_bridge_image_name_map[unique_id, path] = (image, res_image[0])
            core.preview_bridge_cache[unique_id] = (latent, preview_method, vae_opt, res_image)

            res_latent = latent

        if block and is_empty_mask and core.is_execution_model_version_supported():
            from comfy_execution.graph import ExecutionBlocker
            result = ExecutionBlocker(None), ExecutionBlocker(None)
        elif block and is_empty_mask:
            print(f"[Impact Pack] PreviewBridgeLatent: ComfyUI is outdated - blocking feature is disabled.")
            result = res_latent, mask
        else:
            result = res_latent, mask

        if not is_empty_mask:
            core.preview_bridge_last_mask_cache[unique_id] = mask

        return {
            "ui": {"images": res_image},
            "result": result,
        }
