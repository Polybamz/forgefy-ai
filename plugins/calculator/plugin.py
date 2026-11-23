"""Exact arithmetic — small models make mistakes with numbers.

Expressions are parsed and evaluated here, never passed to Python's eval:
only numbers, + - * / // % **, parentheses and a few math functions.
"""
import ast
import math
import operator
from typing import Annotated

from pydantic import Field

from app.plugins.sdk import tool

DESCRIPTION = "Exact arithmetic: totals, percentages, conversions."

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max,
    "floor": math.floor, "ceil": math.ceil, "log": math.log, "log10": math.log10,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(left) > 1 and right * math.log10(abs(left)) > 300:
            raise ValueError("result too large")
        if abs(left) > 1e300 or abs(right) > 1e300:
            raise ValueError("number too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS
            and not node.keywords):
        return _FUNCTIONS[node.func.id](*(_eval(a) for a in node.args))
    raise ValueError(f"not allowed in an expression: {ast.dump(node)[:60]}")


@tool
def calculate(
    expression: Annotated[str, Field(max_length=500, description="e.g. (2500 * 3) * 1.1925 or sqrt(2) / 3")],
) -> str:
    """Evaluate an arithmetic expression exactly. Use it for any calculation instead of doing it in your head."""
    result = _eval(ast.parse(expression, mode="eval"))
    if isinstance(result, float) and result.is_integer() and abs(result) < 1e15:
        result = int(result)
    return f"{expression} = {result}"
