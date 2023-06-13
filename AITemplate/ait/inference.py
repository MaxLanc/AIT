from typing import List

import torch

from .module import Model


class AITemplateModelWrapper(torch.nn.Module):
    def __init__(
        self,
        unet_ait_exe: Model,
        alphas_cumprod: torch.Tensor,
    ):
        super().__init__()
        self.alphas_cumprod = alphas_cumprod
        self.unet_ait_exe = unet_ait_exe

    def apply_model(
        self,
        x: torch.Tensor,
        t: torch.Tensor,
        c_crossattn = None,
        c_concat = None,
        control = None,
        transformer_options = None,
    ):
        timesteps_pt = t
        latent_model_input = x
        encoder_hidden_states = None
        down_block_residuals = None
        mid_block_residual = None
        #TODO: verify this is correct/match DiffusionWrapper (ddpm.py)
        if c_crossattn is not None:
            encoder_hidden_states = c_crossattn
            encoder_hidden_states = encoder_hidden_states[0]
        if c_concat is not None:
            encoder_hidden_states = c_concat
        if control is not None:
            down_block_residuals = control["output"]
            mid_block_residual = control["middle"][0]
        return unet_inference(
            self.unet_ait_exe,
            latent_model_input=latent_model_input,
            timesteps=timesteps_pt,
            encoder_hidden_states=encoder_hidden_states,
            down_block_residuals=down_block_residuals,
            mid_block_residual=mid_block_residual,
        )


def unet_inference(
    exe_module: Model,
    latent_model_input: torch.Tensor,
    timesteps: torch.Tensor,
    encoder_hidden_states: torch.Tensor,
    class_labels: torch.Tensor = None,
    down_block_residuals: List[torch.Tensor] = None,
    mid_block_residual: torch.Tensor = None,
    device: str = "cuda",
    dtype: str = "float16",
):
    batch = latent_model_input.shape[0]
    height, width = latent_model_input.shape[2], latent_model_input.shape[3]
    timesteps_pt = timesteps
    inputs = {
        "input0": latent_model_input.permute((0, 2, 3, 1))
        .contiguous()
        .to(device),
        "input1": timesteps_pt.to(device),
        "input2": encoder_hidden_states.to(device),
    }
    if class_labels is not None:
        inputs["input3"] = class_labels.contiguous().cuda()
    if down_block_residuals is not None and mid_block_residual is not None:
        for i, y in enumerate(down_block_residuals):
            inputs[f"down_block_residual_{i}"] = y.permute((0, 2, 3, 1)).contiguous().to(device)
        inputs["mid_block_residual"] = mid_block_residual.permute((0, 2, 3, 1)).contiguous().to(device)
    if dtype == "float16":
        for k, v in inputs.items():
            if k == "input3":
                continue
            inputs[k] = v.half()
    ys = []
    num_outputs = len(exe_module.get_output_name_to_index_map())
    for i in range(num_outputs):
        shape = exe_module.get_output_maximum_shape(i)
        shape[0] = batch
        shape[1] = height
        shape[2] = width
        ys.append(torch.empty(shape).cuda().half())
    exe_module.run_with_tensors(inputs, ys, graph_mode=False)
    noise_pred = ys[0].permute((0, 3, 1, 2)).float()
    return noise_pred


def controlnet_inference(
    exe_module: Model,
    latent_model_input: torch.Tensor,
    timesteps: torch.Tensor,
    encoder_hidden_states: torch.Tensor,
    controlnet_cond: torch.Tensor,
    device: str = "cuda",
    dtype: str = "float16",
):
    timesteps_pt = timesteps
    inputs = {
        "input0": latent_model_input.permute((0, 2, 3, 1))
        .contiguous()
        .to(device),
        "input1": timesteps_pt.to(device),
        "input2": encoder_hidden_states.to(device),
        "input3": controlnet_cond.permute((0, 2, 3, 1)).contiguous().to(device),
    }
    if dtype == "float16":
        for k, v in inputs.items():
            inputs[k] = v.half()
    ys = []
    num_outputs = len(exe_module.get_output_name_to_index_map())
    for i in range(num_outputs):
        shape = exe_module.get_output_maximum_shape(i)
        ys.append(torch.empty(shape).to(device))
        if dtype == "float16":
            ys[i] = ys[i].half()
    exe_module.run_with_tensors(inputs, ys, graph_mode=False)
    down_block_residuals = (y for y in ys[:-1])
    mid_block_residual = ys[-1]
    return down_block_residuals, mid_block_residual



def vae_inference(
    exe_module: Model,
    vae_input: torch.Tensor,
    factor: int = 8,
    device: str = "cuda",
    dtype: str = "float16",
    encoder: bool = False,
):
    batch = vae_input.shape[0]
    height, width = vae_input.shape[2], vae_input.shape[3]
    inputs = {
        "vae_input": torch.permute(vae_input, (0, 2, 3, 1))
        .contiguous()
        .to(device),
    }
    if dtype == "float16":
        for k, v in inputs.items():
            inputs[k] = v.half()
    ys = []
    num_outputs = len(exe_module.get_output_name_to_index_map())
    for i in range(num_outputs):
        shape = exe_module.get_output_maximum_shape(i)
        shape[0] = batch
        if encoder:
            shape[1] = height // factor
            shape[2] = width // factor
        else:
            shape[1] = height * factor
            shape[2] = width * factor
        ys.append(torch.empty(shape).to(device))
        if dtype == "float16":
            ys[i] = ys[i].half()
    exe_module.run_with_tensors(inputs, ys, graph_mode=False)
    vae_out = ys[0].permute((0, 3, 1, 2)).float()
    return vae_out


def clip_inference(
    exe_module: Model,
    input_ids: torch.Tensor,
    seqlen: int = 77,
    device: str = "cuda",
    dtype: str = "float16",
):
    batch = input_ids.shape[0]
    input_ids = input_ids.to(device)
    position_ids = torch.arange(seqlen).expand((batch, -1)).to(device)
    inputs = {
        "input0": input_ids,
        "input1": position_ids,
    }
    ys = []
    num_outputs = len(exe_module.get_output_name_to_index_map())
    for i in range(num_outputs):
        shape = exe_module.get_output_maximum_shape(i)
        shape[0] = batch
        ys.append(torch.empty(shape).to(device))
        if dtype == "float16":
            ys[i] = ys[i].half()
    exe_module.run_with_tensors(inputs, ys, graph_mode=False)
    return ys[0].float()
