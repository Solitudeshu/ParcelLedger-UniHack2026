"""Paired small decoder with valid-pixel BCE/Dice and disjoint calibration."""
import hashlib
import json
import platform
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from ml.data.appearance_changes import VERSION, example, split_manifest


class ChangeHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.enc=nn.Sequential(nn.Conv2d(10,16,3,padding=1),nn.GELU(),nn.Conv2d(16,16,3,padding=1),nn.GELU())
        self.low=nn.Sequential(nn.Conv2d(16,32,3,padding=1),nn.GELU(),nn.Conv2d(32,32,3,padding=1),nn.GELU())
        self.dec=nn.Sequential(nn.Conv2d(48,16,3,padding=1),nn.GELU(),nn.Conv2d(16,1,1))

    def forward(self,x):
        skip=self.enc(x)
        low=self.low(F.avg_pool2d(skip,2))
        return self.dec(torch.cat([skip,F.interpolate(low,size=skip.shape[-2:],mode="bilinear",align_corners=False)],dim=1))


def loss(logits,target,valid):
    pixel=F.binary_cross_entropy_with_logits(logits,target,reduction="none",pos_weight=torch.tensor(6.,device=logits.device))
    bce=(pixel*valid).sum()/valid.sum().clamp_min(1)
    p=logits.sigmoid()*valid
    t=target*valid
    dims=(1,2,3)
    dice=1-(2*(p*t).sum(dims)+1)/(p.sum(dims)+t.sum(dims)+1)
    return bce+dice.mean()


def measure(probabilities, target, valid, threshold):
    predicted=(probabilities>=threshold)*valid
    target=target*valid
    dims=(1,2,3)
    intersection=(predicted*target).sum(dims)
    union=np.maximum(predicted,target).sum(dims)
    positive=target.sum(dims)>=3
    detected=predicted.sum(dims)>=6
    return {"images":len(target),"positive_images":int(positive.sum()),
        "positive_mean_iou":float(np.mean(intersection[positive]/np.maximum(union[positive],1))) if positive.any() else None,
        "positive_pixel_recall":float(intersection.sum()/max(target.sum(),1)),
        "positive_image_recall":float(detected[positive].mean()) if positive.any() else None,
        "negative_images":int((~positive).sum()),"negative_false_alarms":int(detected[~positive].sum()),
        "negative_false_alarm_rate":float(detected[~positive].mean()) if (~positive).any() else None}


def infer(model,x,device):
    model.eval()
    with torch.inference_mode():
        return np.concatenate([model(torch.from_numpy(x[i:i+16]).to(device)).sigmoid().cpu().numpy() for i in range(0,len(x),16)])


def run(output,boxes=240,epochs=18):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();torch.set_num_threads(2);cv2.setNumThreads(1)
    device="cuda" if torch.cuda.is_available() else "cpu"
    split=split_manifest(boxes)
    values=[example(box,scenario) for box in range(boxes) for scenario in range(4)]
    x,y,v=[np.stack([row[i] for row in values]) for i in range(3)]
    del values
    box_ids=np.repeat(np.arange(boxes),4)
    def indices(kind):
        return np.array([i for i,box in enumerate(box_ids) if split["lineages"][f"appearance:{box}"]==kind])
    train,test=indices("train"),indices("test")
    validation_boxes=sorted({int(box_ids[i]) for i in indices("validation")})
    selection_boxes=set(validation_boxes[::2]);calibration_boxes=set(validation_boxes[1::2])
    selection=np.flatnonzero(np.isin(box_ids,list(selection_boxes)))
    calibration=np.flatnonzero(np.isin(box_ids,list(calibration_boxes)))
    # Seed choice is fixed a priori; epoch selection uses selection loss only.
    torch.manual_seed(26);rng=np.random.default_rng(26)
    model=ChangeHead().to(device);optimizer=torch.optim.AdamW(model.parameters(),lr=.001)
    best=float("inf");best_state=None;logs=[]
    for epoch in range(epochs):
        model.train();losses=[]
        order=rng.permutation(train)
        for start in range(0,len(order),16):
            idx=order[start:start+16]
            logits=model(torch.from_numpy(x[idx]).to(device))
            value=loss(logits,torch.from_numpy(y[idx]).to(device),torch.from_numpy(v[idx]).to(device))
            optimizer.zero_grad();value.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);optimizer.step()
            losses.append(float(value.detach()))
        model.eval();selection_losses=[]
        with torch.inference_mode():
            for start in range(0,len(selection),16):
                idx=selection[start:start+16]
                selection_losses.append(float(loss(model(torch.from_numpy(x[idx]).to(device)),torch.from_numpy(y[idx]).to(device),torch.from_numpy(v[idx]).to(device))))
        validation=float(np.mean(selection_losses))
        logs.append({"epoch":epoch+1,"train_loss":float(np.mean(losses)),"selection_loss":validation})
        print(json.dumps(logs[-1]),flush=True)
        if validation<best:
            best=validation;best_state={key:value.detach().cpu().clone() for key,value in model.state_dict().items()}
    model.load_state_dict(best_state)
    probabilities=infer(model,x[calibration],device)
    candidates=[]
    for threshold in (.3,.4,.5,.6,.7,.8,.9,.95):
        metrics=measure(probabilities,y[calibration],v[calibration],threshold)
        if metrics["negative_false_alarm_rate"] <= .05:
            candidates.append((metrics["positive_mean_iou"],threshold))
    threshold=max(candidates)[1] if candidates else 1.
    prediction=infer(model,x[test],device)
    metrics=measure(prediction,y[test],v[test],threshold)
    residual=np.max(x[test,6:9],axis=1,keepdims=True)
    residual_candidates=[]
    for cutoff in (.05,.08,.12,.16,.20,.30):
        m=measure(np.max(x[calibration,6:9],axis=1,keepdims=True),y[calibration],v[calibration],cutoff)
        if m["negative_false_alarm_rate"]<=.05:residual_candidates.append((m["positive_mean_iou"],cutoff))
    residual_threshold=max(residual_candidates)[1] if residual_candidates else 1.
    torch.save(best_state,output/"change-head.pt")
    probe=x[selection[:3]]
    with torch.inference_mode(): logits=model(torch.from_numpy(probe).to(device)).cpu().numpy()
    np.savez_compressed(output/"export-parity.npz",features=probe,logits=logits)
    card={"model_version":"synthetic-change-head-v1","dataset":VERSION,"source_kind":"SYNTHETIC",
        "real_image_validation":"NOT_RUN","release_gate":"HOLD_REAL_BOX_VALIDATION","boxes":boxes,"images":len(x),
        "split":split,"selection_boxes":sorted(selection_boxes),"calibration_boxes":sorted(calibration_boxes),
        "threshold":threshold,"selection_loss":best,"calibration_policy":"max positive IoU subject to <=5% negative alarms",
        "test":metrics,"residual_baseline":{"threshold":residual_threshold,**measure(residual,y[test],v[test],residual_threshold)},
        "checkpoint_sha256":hashlib.sha256((output/"change-head.pt").read_bytes()).hexdigest(),
        "runtime":{"python":platform.python_version(),"torch":torch.__version__,"seconds":time.monotonic()-started,"device":device},
        "limitations":["Procedural texture changes only; no physical dent diagnosis.","Generated ignore masks; no trained occlusion detector.","Subpixel alignment nuisance only; no guarantee under real nonplanar deformation.","No physical-box evaluation; cannot determine responsibility or compensation."]}
    (output/"model-card.json").write_text(json.dumps(card,indent=2),encoding="utf-8")
    (output/"training-log.json").write_text(json.dumps(logs,indent=2),encoding="utf-8")
    print(json.dumps(card),flush=True)
    return card
