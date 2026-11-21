import pytest
import torch
from torch import nn

from ml.data.upgrade_audit import assign_roles, duplicate_groups
from ml.models.paired_transformer import LoRALinear, PairedChangeTransformer, masked_change_loss


def test_lora_starts_as_original_and_trains_without_mutating_base():
    torch.manual_seed(7)
    base = nn.Linear(12, 36)
    original = base.weight.detach().clone()
    adapter = LoRALinear(base, rank=4)
    x = torch.randn(2, 5, 12)
    assert torch.equal(adapter(x), base(x))
    optimizer = torch.optim.AdamW([p for p in adapter.parameters() if p.requires_grad], lr=.01)
    adapter(x).square().mean().backward()
    assert adapter.up.weight.grad.abs().sum() > 0
    assert base.weight.grad is None
    optimizer.step()
    assert torch.equal(base.weight, original)
    assert not torch.equal(adapter(x), base(x))


def test_spatial_candidate_conditions_on_reference_and_backpropagates():
    torch.manual_seed(26)
    torch.set_num_threads(2)
    model = PairedChangeTransformer(encoder_channels=16, width=32, levels=2)
    q = [torch.randn(2, 16, 4, 4) for _ in range(2)]
    r = [torch.randn(2, 16, 4, 4) for _ in range(2)]
    image = torch.rand(2, 3, 56, 56)
    valid = torch.ones(2, 1, 56, 56)
    valid[1] = 0  # No overlap: attention and loss must remain finite.
    prediction = model(q, r, image, image, valid)
    changed_reference = model(q, [x + 1 for x in r], image, image, valid)
    assert prediction.shape == valid.shape
    assert torch.isfinite(prediction).all()
    assert not torch.allclose(prediction[0], changed_reference[0])
    target = torch.zeros_like(valid)
    target[0, :, 15:25, 15:35] = 1
    value = masked_change_loss(prediction, target, valid)
    value.backward()
    assert model.cross_view[0].attention.in_proj_weight.grad.abs().sum() > 0
    assert model.detail[0].weight.grad.abs().sum() > 0


def test_unknown_supervision_does_not_become_negative_or_create_gradients():
    logits = torch.randn(2, 1, 28, 28, requires_grad=True)
    target = torch.zeros_like(logits)
    valid = torch.ones_like(logits)
    valid[:, :, :10] = 0
    first = masked_change_loss(logits, target, valid)
    target[:, :, :10] = 1
    second = masked_change_loss(logits, target, valid)
    assert torch.equal(first, second)
    second.backward()
    assert logits.grad[:, :, :10].count_nonzero() == 0
    empty = masked_change_loss(logits, target, torch.zeros_like(valid))
    assert empty.item() == 0


def test_high_resolution_half_loss_has_no_reduction_overflow():
    logits = torch.zeros(1, 1, 518, 518, dtype=torch.float16, requires_grad=True)
    target = torch.ones_like(logits)
    value = masked_change_loss(logits, target, torch.ones_like(logits))
    assert 1 < value.item() < 1.1
    value.backward()
    assert torch.isfinite(logits.grad).all()
    assert logits.grad.abs().sum() > 0


def test_official_test_and_object_roles_are_preserved():
    rows = [{"lineage_id": f"box{i}", "source_split": split}
            for i, split in enumerate(["train", "validation", "validation", "test"])]
    roles = assign_roles(rows)
    assert roles["box0"] == "train" and roles["box3"] == "test"
    assert {roles["box1"], roles["box2"]} == {"selection", "calibration"}
    assert assign_roles(list(reversed(rows))) == roles
    with pytest.raises(ValueError, match="LINEAGE_LEAKAGE"):
        assign_roles(rows + [{"lineage_id": "box3", "source_split": "train"}])


def test_decoded_duplicates_catch_reencoded_cross_split_image():
    rows = [{"image_path": "a.png", "role": "train", "sha256": "different1", "pixel_sha256": "same"},
            {"image_path": "b.jpg", "role": "test", "sha256": "different2", "pixel_sha256": "same"}]
    assert duplicate_groups(rows, "sha256") == []
    assert duplicate_groups(rows, "pixel_sha256")[0]["roles"] == ["test", "train"]
