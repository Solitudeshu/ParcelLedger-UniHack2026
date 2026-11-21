"""Self-generated visible-change pairs with nuisance/ignore masks.

This is synthetic appearance-change pretraining, not a real-damage benchmark.
No label claims a dent, a cause, or responsibility.
"""
import cv2
import numpy as np

from ml.data.carton_views import face_texture
from ml.data.labels import locked_split

VERSION = "carton-appearance-change-v1"
SIZE = 128


def split_manifest(boxes=240):
    return locked_split([f"appearance:{i}" for i in range(boxes)], seed=VERSION)


def example(box, scenario):
    rng = np.random.default_rng(np.random.SeedSequence([262611, box, scenario]))
    before = cv2.resize(face_texture(20000+box, box % 3), (SIZE, SIZE))
    after = before.copy()
    mask = np.zeros((SIZE, SIZE), np.uint8)
    valid = np.ones_like(mask)
    changed = scenario % 2 == 1
    if changed:
        center = rng.integers(25, 104, 2)
        if box % 2:
            points = center + rng.integers(-18, 19, (7, 2))
            points = cv2.convexHull(points.astype(np.int32))
            cv2.fillConvexPoly(mask, points, 1)
        else:
            end = center+rng.integers(-22, 23, 2)
            cv2.line(mask, tuple(center), tuple(end), 1, int(rng.integers(2, 7)))
        color = rng.integers(25, 80, 3) if box % 3 else rng.integers(190, 246, 3)
        after[mask > 0] = color
    # Continuous lighting variation across the face; no change label is added.
    gradient = np.linspace(rng.uniform(.8, 1.1), rng.uniform(.8, 1.1), SIZE)[None, :, None]
    after = np.clip(after.astype(np.float32)*gradient*rng.uniform(.75,1.25)+rng.uniform(-10,10),0,255).astype(np.uint8)
    # Subpixel registration residual is a hard negative around printed edges.
    shift = np.float32([[1,0,rng.uniform(-.65,.65)],[0,1,rng.uniform(-.65,.65)]])
    before = cv2.warpAffine(before,shift,(SIZE,SIZE),borderMode=cv2.BORDER_REFLECT_101)
    valid[:3]=valid[-3:]=0
    valid[:,:3]=valid[:,-3:]=0
    if scenario >= 2:
        x,y=(int(v) for v in rng.integers(15,90,2))
        cv2.rectangle(after,(x,y),(x+23,y+25),(255,255,255) if scenario == 2 else (75,80,90),-1)
        valid[max(0,y-2):min(SIZE,y+28),max(0,x-2):min(SIZE,x+26)]=0
    # Correct global illumination without looking at the change label.
    corrected=before.astype(np.float32)
    for c in range(3):
        r,q=corrected[:,:,c][valid>0],after[:,:,c][valid>0].astype(np.float32)
        rq,qq=np.percentile(r,[20,50,80]),np.percentile(q,[20,50,80])
        gain=np.clip((qq[2]-qq[0])/max(rq[2]-rq[0],10),.5,2)
        corrected[:,:,c]=corrected[:,:,c]*gain+qq[1]-rq[1]*gain
    r=np.clip(corrected,0,255)/255
    q=after.astype(np.float32)/255
    features=np.concatenate([q,r,np.abs(q-r),valid[:,:,None]],axis=2).transpose(2,0,1).astype(np.float32)
    return features, mask[None].astype(np.float32), valid[None].astype(np.float32)
