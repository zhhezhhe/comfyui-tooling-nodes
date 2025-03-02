from PIL import Image
import numpy as np
import base64
import torch
from io import BytesIO
from server import PromptServer, BinaryEventTypes

def process_image_base64(image_base64):
    try:
        imgdata = base64.b64decode(image_base64)
        img = Image.open(BytesIO(imgdata))
    except Exception as e:
        print(f"Error decoding or opening the image: {e}")
        return None, None

    try:
        if "A" in img.getbands():
            # print("Found alpha channel")
            mask = np.array(img.getchannel("A")).astype(np.float32) / 255.0
            mask = 1.0 - torch.from_numpy(mask)
        else:
            # print("No alpha channel found")
            mask = torch.zeros((img.height, img.width), dtype=torch.float32, device="cpu")

        img = img.convert("RGB")
        img_array = np.array(img).astype(np.float32) / 255.0
        img_tensor = torch.from_numpy(img_array).unsqueeze(0)
    except Exception as e:
        print(f"Error processing the image: {e}")
        return None, None

    return img_tensor, mask


class LoadImagesBase64:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "images_base64_str": ("STRING", {"multiline": True}),
            }
        }
    RETURN_TYPES = ("IMAGE", "MASK")
    CATEGORY = "_external_tooling"
    FUNCTION = "load_images"

    def load_images(self, images_base64_str):
        # Split the multiline Base64 string into a list of individual Base64 strings
        images_base64_list = images_base64_str.strip().split('\n')
        image_list = []
        mask_list = []

        for image_base64 in images_base64_list:
            try:
                img_tensor, mask = process_image_base64(image_base64)
                image_list.append(img_tensor)
                mask_list.append(mask)
            except Exception as e:
                # Handle exceptions from faulty base64 strings
                print(f"Error processing base64 image: {e}")
                continue  # Skip this image and continue with the next

        if len(image_list) == 0:
            raise FileNotFoundError("No images could be loaded from the provided Base64 string.")

        return (torch.cat(image_list, dim=0), torch.stack(mask_list, dim=0))

class LoadImageBase64:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"image": ("STRING", {"multiline": True})}}

    RETURN_TYPES = ("IMAGE", "MASK")
    CATEGORY = "_external_tooling"
    FUNCTION = "load_image"

    def load_image(self, image):
        img, mask = process_image_base64(image)
        return (img, mask)


class LoadMaskBase64:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"mask": ("STRING", {"multiline": True})}}

    RETURN_TYPES = ("MASK",)
    CATEGORY = "_external_tooling"
    FUNCTION = "load_mask"

    def load_mask(self, mask):
        imgdata = base64.b64decode(mask)
        img = Image.open(BytesIO(imgdata))
        img = np.array(img).astype(np.float32) / 255.0
        img = torch.from_numpy(img)
        if img.dim() == 3:  # RGB(A) input, use red channel
            img = img[:, :, 0]
        return (img,)


class SendImageWebSocket:
    @classmethod
    def INPUT_TYPES(s):
        return {"required": {"images": ("IMAGE",)}}

    RETURN_TYPES = ()
    FUNCTION = "send_images"
    OUTPUT_NODE = True
    CATEGORY = "_external_tooling"

    def send_images(self, images):
        results = []
        for tensor in images:
            array = 255.0 * tensor.cpu().numpy()
            image = Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))

            server = PromptServer.instance
            server.send_sync(
                BinaryEventTypes.UNENCODED_PREVIEW_IMAGE,
                ["PNG", image, None],
                server.client_id,
            )
            results.append(
                # Could put some kind of ID here, but for now just match them by index
                {"source": "websocket", "content-type": "image/png", "type": "output"}
            )

        return {"ui": {"images": results}}


class CropImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "x": (
                    "INT",
                    {"default": 0, "min": 0, "max": 8192, "step": 1},
                ),
                "y": (
                    "INT",
                    {"default": 0, "min": 0, "max": 8192, "step": 1},
                ),
                "width": (
                    "INT",
                    {"default": 512, "min": 1, "max": 8192, "step": 1},
                ),
                "height": (
                    "INT",
                    {"default": 512, "min": 1, "max": 8192, "step": 1},
                ),
            }
        }

    CATEGORY = "_external_tooling"
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "crop"

    def crop(self, image, x, y, width, height):
        out = image[:, y : y + height, x : x + width, :]
        return (out,)


class ApplyMaskToImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "mask": ("MASK",),
            }
        }

    CATEGORY = "_external_tooling"
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "apply_mask"

    def apply_mask(self, image, mask):
        out = image.movedim(-1, 1)
        if out.shape[1] == 3:  # RGB
            out = torch.cat([out, torch.ones_like(out[:, :1, :, :])], dim=1)
        for i in range(out.shape[0]):
            out[i, 3, :, :] = mask
        out = out.movedim(1, -1)
        return (out,)
