"""Just enough of the ONNX wire format to explain a score without the `onnx` package.

`chester.saliency` needs two things from the model artifact that scoring itself
does not: the pre-pool activation added to the graph's outputs, so ONNX Runtime
will hand it back, and the classifier matrix read out of the initializers. The
`onnx` package does both in three lines -- and that is what this module is used
instead of only when it is missing.

It is missing more often than a dependency list suggests. `onnxruntime` is a
self-contained wheel with no `onnx` dependency, so an environment that installs
what it needs to *run* the model has everything except this, and a deployment
that installs its Python packages once and then updates only the application
carries the gap forward. The symptom is specific and misleading: every score is
correct, and the explanation is the one feature that is silently absent.

So the two reads are implemented here against the wire format directly. Both are
narrow -- a repeated message scan and one appended field -- and both are checked
against the `onnx` package's own answer in tests/test_onnx_graph.py wherever it
is installed, which is the only thing that makes hand-rolled protobuf defensible.

Field numbers are from onnx/onnx.proto3:

    ModelProto.graph              7
    GraphProto.node               1   NodeProto.output       2
    GraphProto.initializer        5   TensorProto.dims       1
    GraphProto.output            12                 .data_type   2
    ValueInfoProto.name           1                 .float_data  4
    ValueInfoProto.type           2                 .name        8
    TypeProto.tensor_type         1                 .raw_data    9
    TypeProto.Tensor.elem_type    1
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Iterator

import numpy as np

logger = logging.getLogger(__name__)

WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LENGTH = 2
WIRE_32BIT = 5

# TensorProto.DataType values this module can return. Everything the classifier
# of this model is stored as, and nothing else: an artifact holding its weights
# as float16 or bfloat16 would come back unread rather than reinterpreted.
FLOAT = 1
DOUBLE = 11

_DTYPES: dict[int, str] = {FLOAT: "<f4", DOUBLE: "<f8"}


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("Truncated varint")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7
        if shift > 63:
            raise ValueError("Varint is longer than 64 bits")


def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _fields(data: bytes) -> Iterator[tuple[int, int, int, int]]:
    """Yield (field_number, wire_type, value_start, next_offset) for one message.

    The payload is returned as a slice rather than decoded: every caller here
    wants either the raw bytes of a submessage or to skip the field entirely.
    """
    offset = 0
    end = len(data)
    while offset < end:
        key, offset = _read_varint(data, offset)
        field, wire = key >> 3, key & 0x07
        start = offset
        if wire == WIRE_VARINT:
            _, offset = _read_varint(data, offset)
        elif wire == WIRE_64BIT:
            offset += 8
        elif wire == WIRE_LENGTH:
            length, offset = _read_varint(data, offset)
            start = offset
            offset += length
        elif wire == WIRE_32BIT:
            offset += 4
        else:
            raise ValueError(f"Unsupported wire type {wire} for field {field}")
        if offset > end:
            raise ValueError("Truncated message")
        yield field, wire, start, offset


def _submessages(data: bytes, field_number: int) -> Iterator[bytes]:
    for field, wire, start, end in _fields(data):
        if field == field_number and wire == WIRE_LENGTH:
            yield data[start:end]


def _strings(data: bytes, field_number: int) -> Iterator[str]:
    for message in _submessages(data, field_number):
        yield message.decode("utf-8", "replace")


def _scalar(data: bytes, field_number: int) -> int | None:
    for field, wire, start, _ in _fields(data):
        if field == field_number and wire == WIRE_VARINT:
            return _read_varint(data, start)[0]
    return None


def _graph(model: bytes) -> tuple[int, int] | None:
    """Where ModelProto.graph's payload starts and ends, or None."""
    for field, wire, start, end in _fields(model):
        if field == 7 and wire == WIRE_LENGTH:
            return start, end
    return None


def _value_info(name: str, elem_type: int = FLOAT) -> bytes:
    """A ValueInfoProto for a float tensor of unstated shape.

    No TensorShapeProto: the activation's spatial size follows from the input,
    and a shape written here would be a second claim about it that could be
    wrong. `onnx.helper.make_tensor_value_info(name, FLOAT, None)` omits it too.
    """
    tensor = _write_varint(1 << 3 | WIRE_VARINT) + _write_varint(elem_type)
    type_proto = _write_varint(1 << 3 | WIRE_LENGTH) + _write_varint(len(tensor)) + tensor
    encoded_name = name.encode("utf-8")
    return (
        _write_varint(1 << 3 | WIRE_LENGTH)
        + _write_varint(len(encoded_name))
        + encoded_name
        + _write_varint(2 << 3 | WIRE_LENGTH)
        + _write_varint(len(type_proto))
        + type_proto
    )


def produces(model: bytes, tensor_name: str) -> bool:
    """Whether any node in the graph writes this tensor."""
    bounds = _graph(model)
    if bounds is None:
        return False
    graph = model[bounds[0] : bounds[1]]
    return any(tensor_name in set(_strings(node, 2)) for node in _submessages(graph, 1))


def graph_outputs(model: bytes) -> list[str]:
    """The names of GraphProto.output, in order."""
    bounds = _graph(model)
    if bounds is None:
        return []
    graph = model[bounds[0] : bounds[1]]
    names: list[str] = []
    for output in _submessages(graph, 12):
        names.extend(_strings(output, 1))
    return names


def with_extra_output(model: bytes, tensor_name: str) -> bytes | None:
    """The model with `tensor_name` appended to its graph outputs.

    None when no node produces it, which is the caller's signal to carry on with
    the artifact as it stands and do without explanations. Returns the model
    unchanged when it is already an output.

    Only ModelProto.graph is rewritten, and only by appending one field to it:
    every other byte of the artifact, the weights above all, is copied through.
    """
    bounds = _graph(model)
    if bounds is None:
        raise ValueError("Not an ONNX model: no graph field")
    if not produces(model, tensor_name):
        return None
    if tensor_name in graph_outputs(model):
        return model

    start, end = bounds
    graph = model[start:end]
    value_info = _value_info(tensor_name)
    extended = (
        graph + _write_varint(12 << 3 | WIRE_LENGTH) + _write_varint(len(value_info)) + value_info
    )

    # The graph is a length-delimited field, so its own header has to be rewritten
    # with the new length; the key is the byte(s) before the length varint, which
    # `_fields` has already stepped past. Rebuilding from the key forward keeps
    # everything on either side of the graph byte-for-byte.
    key = _write_varint(7 << 3 | WIRE_LENGTH)
    header_start = start - len(_write_varint(len(graph))) - len(key)
    if model[header_start : header_start + len(key)] != key:
        raise ValueError("Could not locate the graph field header")

    return (
        model[:header_start]
        + key
        + _write_varint(len(extended))
        + extended
        # Everything after the graph, opset_import above all: a model that lost it
        # parses and then fails the checker with a message about IR versions.
        + model[end:]
    )


def initializer(model: bytes, name: str) -> np.ndarray | None:
    """One initializer, as an array, or None when it is absent or unreadable.

    Reads `raw_data` and packed `float_data`, which is how every exporter in use
    writes a float weight. An initializer stored some other way -- externally, or
    in a type this does not map -- comes back None rather than half-decoded.
    """
    bounds = _graph(model)
    if bounds is None:
        return None
    graph = model[bounds[0] : bounds[1]]

    for tensor in _submessages(graph, 5):
        if next(_strings(tensor, 8), None) != name:
            continue

        dims: list[int] = []
        for field, wire, start, end in _fields(tensor):
            if field != 1:
                continue
            if wire == WIRE_VARINT:
                dims.append(_read_varint(tensor, start)[0])
            elif wire == WIRE_LENGTH:  # packed
                offset = start
                while offset < end:
                    value, offset = _read_varint(tensor, offset)
                    dims.append(value)

        data_type = _scalar(tensor, 2)
        raw = next(_submessages(tensor, 9), None)

        if raw is not None:
            dtype = _DTYPES.get(data_type or 0)
            if dtype is None:
                logger.warning("Initializer %s has unsupported data type %s", name, data_type)
                return None
            values = np.frombuffer(raw, dtype=dtype).astype(np.float32)
        elif data_type == FLOAT:
            floats: list[float] = []
            for field, wire, start, end in _fields(tensor):
                if field != 4:
                    continue
                if wire == WIRE_32BIT:
                    floats.append(struct.unpack_from("<f", tensor, start)[0])
                elif wire == WIRE_LENGTH:  # packed
                    count = (end - start) // 4
                    floats.extend(struct.unpack_from(f"<{count}f", tensor, start))
            values = np.asarray(floats, dtype=np.float32)
        else:
            logger.warning("Initializer %s carries no data this can read", name)
            return None

        if dims and int(np.prod(dims)) == values.size:
            return values.reshape(dims)
        return values

    return None
