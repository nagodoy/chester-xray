"""Explanations: the map must be the model's own decision, written out per position."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from chester import inference, saliency
from chester.inference import IMAGE_SIZE, PATHOLOGIES
from chester.security.roles import ROLE_ADMIN

# An output this fixture does argue *for*. Which outputs a synthetic raster excites
# is a property of the weights, not something to assume: `Mass` comes back with no
# positive position at all on it, which is what test_an_unexcited_output_is_empty
# pins down.
EXCITED = "Cardiomegaly"


@pytest.fixture
def chest() -> np.ndarray:
    """A 0..255 raster with some structure, which is what load_pixels returns."""
    rng = np.random.default_rng(11)
    base = rng.random((320, 260), dtype=np.float32) * 40.0 + 80.0
    base[80:200, 60:120] += 90.0
    return np.clip(base, 0.0, 255.0)


def test_the_map_reconstructs_the_model_logit(chest):
    """The property the whole feature rests on.

    The map's spatial mean, plus the classifier bias, is the logit the worklist
    scored. If this drifts, the picture has stopped describing the decision and
    is just a coloured blob.
    """
    import onnx
    from onnx import numpy_helper

    model = onnx.load(str(inference._model_path()))
    bias = next(
        numpy_helper.to_array(i)
        for i in model.graph.initializer
        if i.name == "inner.classifier.bias"
    )
    weights = inference.classifier_weights()

    prepared = inference.preprocess(chest).reshape(1, 1, IMAGE_SIZE, IMAGE_SIZE)
    maps = inference.activation(prepared)
    logits = inference.get_session().run(["scores"], {"image": prepared})[0].reshape(-1)

    for pathology in ("Mass", "Cardiomegaly", "Effusion"):
        index = PATHOLOGIES.index(pathology)
        contribution = np.einsum("k,kxy->xy", weights[index], maps)
        recovered = float(contribution.mean() + bias[index])
        expected = float(np.log(logits[index] / (1.0 - logits[index])))
        assert recovered == pytest.approx(expected, abs=1e-4)


def test_exposing_the_activation_does_not_change_the_scores(chest):
    """Bit-identical, not merely close.

    The rewritten graph is what every score in the database now comes from, so
    this is a parity check in the sense docs/onnx-parity.md means.
    """
    import onnxruntime as ort

    prepared = inference.preprocess(chest).reshape(1, 1, IMAGE_SIZE, IMAGE_SIZE)
    through_rewritten = inference.get_session().run(["scores"], {"image": prepared})[0]

    plain = ort.InferenceSession(str(inference._model_path()), providers=["CPUExecutionProvider"])
    through_plain = plain.run(["scores"], {"image": prepared})[0]

    assert np.array_equal(through_rewritten, through_plain)


def test_map_is_bounded_and_peaks_at_one(chest):
    values = saliency.activation_map(chest, EXCITED)
    assert values.shape == (IMAGE_SIZE, IMAGE_SIZE)
    assert values.min() >= 0.0
    assert values.max() == pytest.approx(1.0, abs=1e-3)


def test_a_map_with_no_positive_evidence_is_empty():
    """Nothing to point at must render as nothing, not as stretched noise."""
    empty = saliency._to_unit_scale(np.full((7, 7), -3.0, dtype=np.float32))
    assert not empty.any()
    # Still the display size: a map that changed shape when it had nothing to say
    # would be a trap for every caller.
    assert empty.shape == (IMAGE_SIZE, IMAGE_SIZE)


def test_an_unexcited_output_is_empty(chest):
    """Reached through the real path, not only through the helper."""
    values = saliency.activation_map(chest, "Mass")
    assert values.shape == (IMAGE_SIZE, IMAGE_SIZE)
    assert not values.any()


def test_suppressed_outputs_are_not_explained(chest):
    for pathology in ("Fracture", "Pneumonia"):
        if inference.is_reported(pathology):
            continue
        with pytest.raises(ValueError, match="not an output this deployment explains"):
            saliency.activation_map(chest, pathology)


def test_overlay_is_a_square_png_of_the_scored_crop(chest):
    data = saliency.overlay_png(chest, EXCITED)
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG"
        assert image.size == (saliency.RENDER_SIZE, saliency.RENDER_SIZE)
        assert image.mode == "RGB"


def test_overlay_tints_where_the_evidence_is(chest):
    """The tint has to follow the map, not sit uniformly over the picture."""
    heat = saliency.activation_map(chest, EXCITED)
    with Image.open(io.BytesIO(saliency.overlay_png(chest, EXCITED))) as image:
        rendered = np.asarray(image.resize((IMAGE_SIZE, IMAGE_SIZE), Image.BILINEAR), np.float32)

    blueness = rendered[..., 2] - rendered[..., 0]
    hot = heat > 0.8
    cold = heat < 0.1
    assert hot.any() and cold.any()
    assert blueness[hot].mean() > blueness[cold].mean() + 20.0


@pytest.fixture
def analysed_study(session, make_user, make_png):
    """A study with a stored PNG instance, which is what the endpoint renders."""
    from chester.models import Instance, Study
    from chester.storage import store_bytes

    owner = make_user("reader@example.com", ROLE_ADMIN)
    study = Study(
        owner_user_id=owner.id,
        organization_id=owner.organization_id,
        status="completed",
        source="upload",
    )
    session.add(study)
    session.flush()

    key = f"instances/{study.id}.png"
    store_bytes(key, make_png(rows=256, columns=256), "image/png", session=session)
    session.add(
        Instance(
            study_id=study.id,
            organization_id=owner.organization_id,
            object_key=key,
            content_type="image/png",
            frame_count=1,
        )
    )
    session.flush()
    return study


def test_endpoint_returns_a_png(client, signed_in, analysed_study):
    from chester.api import explanations

    explanations.reset_cache()
    headers = signed_in("reader@example.com")[0]

    response = client.get(f"/api/studies/{analysed_study.id}/explain/Cardiomegaly", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert "private" in response.headers["cache-control"]
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (saliency.RENDER_SIZE, saliency.RENDER_SIZE)


def test_endpoint_refuses_a_suppressed_output(client, signed_in, analysed_study):
    headers = signed_in("reader@example.com")[0]
    response = client.get(f"/api/studies/{analysed_study.id}/explain/Fracture", headers=headers)
    assert response.status_code == 404


def test_endpoint_refuses_an_unknown_output(client, signed_in, analysed_study):
    headers = signed_in("reader@example.com")[0]
    response = client.get(f"/api/studies/{analysed_study.id}/explain/Sarcoidosis", headers=headers)
    assert response.status_code == 404


def test_endpoint_needs_a_session(client, analysed_study):
    assert client.get(f"/api/studies/{analysed_study.id}/explain/Cardiomegaly").status_code == 401


def test_a_study_you_cannot_see_is_not_explained(client, signed_in, make_user, analysed_study):
    """Visibility has to gate the picture as tightly as it gates the study."""
    from chester.security.roles import ROLE_TECHNICIAN

    make_user("outsider@example.com", ROLE_TECHNICIAN)
    headers = signed_in("outsider@example.com")[0]

    response = client.get(f"/api/studies/{analysed_study.id}/explain/Cardiomegaly", headers=headers)
    assert response.status_code == 404


def test_explanations_survive_without_the_onnx_package(monkeypatch, chest):
    """The environment this feature was actually absent in.

    `onnxruntime` does not depend on `onnx`, so a deployment can score every study
    correctly and have no way to read or rewrite the artifact. It used to mean the
    session loaded without the activation output and every explanation answered
    503 -- which looks, from the worklist, like a feature nobody built.
    """
    import sys

    monkeypatch.setitem(sys.modules, "onnx", None)  # `import onnx` now raises ImportError
    inference.reset_session()
    try:
        assert inference.activation_available()

        prepared = inference.preprocess(chest).reshape(1, 1, IMAGE_SIZE, IMAGE_SIZE)
        channels = inference.activation(prepared).shape[0]
        assert inference.classifier_weights().shape == (len(PATHOLOGIES), channels)

        overlay = saliency.overlay_png(chest, EXCITED)
        with Image.open(io.BytesIO(overlay)) as image:
            assert image.size == (saliency.RENDER_SIZE, saliency.RENDER_SIZE)
    finally:
        # The session cached above holds a graph built without the package; the
        # next test should load it the way the application does.
        inference.reset_session()
