"""Safe math expression evaluator tool."""

import ast
import math
import operator as op

from langchain_core.tools import tool

_ALLOWED_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.Mod: op.mod,
    ast.FloorDiv: op.floordiv,
}

_SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e,
}


def _eval(node: ast.expr) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in _SAFE_NAMES:
            return _SAFE_NAMES[node.id]  # type: ignore[return-value]
        raise ValueError(f"Unknown name: {node.id}")
    if isinstance(node, ast.BinOp):
        fn = _ALLOWED_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported operator: {node.op}")
        return fn(_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        fn = _ALLOWED_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported unary operator: {node.op}")
        return fn(_eval(node.operand))  # type: ignore[call-arg]
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _SAFE_NAMES:
            fn = _SAFE_NAMES[node.func.id]
            args = [_eval(a) for a in node.args]
            return fn(*args)  # type: ignore[operator]
        raise ValueError(f"Unsafe function call: {ast.dump(node.func)}")
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression and return the result.

    Supports: +, -, *, /, **, %, //, abs, round, min, max,
              sqrt, log, log10, sin, cos, tan, pi, e.

    Args:
        expression: A safe math expression, e.g. "sqrt(144) + 3 * pi"
    """
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree.body)
        return str(result)
    except Exception as exc:
        return f"Error evaluating expression: {exc}"
