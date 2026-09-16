"""The hand-rolled ONNX reads, checked against the `onnx` package itself.

`chester.onnx_graph` exists so an explanation can be drawn in an environment that
has `onnxruntime` and not `onnx`, which is the ordinary shape of a deployment.
Hand-rolled protobuf is only defensible while something holds it to the real
implementation's answer, and that is what this file is: wherever `onnx` is
installed -- it is a dev dependency, so in CI always -- the rewritten model must
come back byte for byte identical to the one that package produces, and every
initializer must read the same.
"""

from __future__ import annotations

import numpy as np
import pytest

from chester import inference, onnx_graph
from chester.inference import ACTIVATION_OUTPUT, CLASSIFIER_WEIGHT

onnx = pytest.importorskip("onnx", reason="the comparison needs the package being compared to")


@pytest.fixture(scope="module")
def artifact() -> bytes:
    return inference._model_path().read_bytes()


def test_the_rewrite_is_byte_identical_to_the_onnx_package(artifact):
    """The whole claim. Same bytes, so the same graph, weights and metadata."""
    from onnx import helper

    model = onnx.load_from_string(artifact)
    model.graph.output.extend(
        [helper.make_tensor_value_info(ACTIVATION_OUTPUT, onnx.TensorProto.FLOAT, None)]
    )

    assert onnx_graph.with_extra_output(artifact, ACTIVATION_OUTPUT) == model.SerializeToString()


def test_the_rewrite_keeps_what_follows_the_graph(artifact):
    """opset_import sits after the graph, and a model that lost it will not load."""
    rewritten = onnx.load_from_string(onnx_graph.with_extra_output(artifact, ACTIVATION_OUTPUT))
    original = onnx.load_from_string(artifact)

    assert [(imp.domain, imp.version) for imp in rewritten.opset_import] == [
        (imp.domain, imp.version) for imp in original.opset_import
    ]
    assert rewritten.ir_version == original.ir_version
    assert len(rewritten.graph.initializer) == len(original.graph.initializer)


def test_initializers_read_the_same_as_the_package(artifact):
    from onnx import numpy_helper

    model = onnx.load_from_string(artifact)
    for name in (CLASSIFIER_WEIGHT, "inner.classifier.bias"):
        expected = numpy_helper.to_array(
            next(item for item in model.graph.initializer if item.name == name)
        )
        actual = onnx_graph.initializer(artifact, name)
        assert actual is not None
        assert actual.shape == expected.shape
        assert np.array_equal(actual, expected)


def test_an_absent_initializer_is_none_rather_than_an_error(artifact):
    assert onnx_graph.initializer(artifact, "no.such.tensor") is None


def test_a_tensor_no_node_produces_cannot_be_added(artifact):
    """The signal that a model artifact simply cannot be explained."""
    assert not onnx_graph.produces(artifact, "/Nothing_output_0")
    assert onnx_graph.with_extra_output(artifact, "/Nothing_output_0") is None


def test_adding_an_output_twice_changes_nothing(artifact):
    once = onnx_graph.with_extra_output(artifact, ACTIVATION_OUTPUT)
    assert onnx_graph.with_extra_output(once, ACTIVATION_OUTPUT) == once


def test_the_outputs_are_read_in_order(artifact):
    assert onnx_graph.graph_outputs(artifact) == ["scores"]
    rewritten = onnx_graph.with_extra_output(artifact, ACTIVATION_OUTPUT)
    assert onnx_graph.graph_outputs(rewritten) == ["scores", ACTIVATION_OUTPUT]


def test_something_that_is_not_a_model_is_refused(artifact):
    with pytest.raises(ValueError):
        onnx_graph.with_extra_output(b"not a protobuf at all", ACTIVATION_OUTPUT)
