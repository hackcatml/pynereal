import ast
from typing import cast


class RequestSecurityTransformer(ast.NodeTransformer):
    """
    Wrap the 3rd argument of request.security(...) in a zero-arg lambda for lazy evaluation.
    """

    @staticmethod
    def _is_request_security_call(node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "security":
            return False
        value = node.func.value
        if isinstance(value, ast.Name) and value.id == "request":
            return True
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            return value.value.id == "lib" and value.attr == "request"
        return False

    def visit_Call(self, node: ast.Call) -> ast.AST:
        node = cast(ast.Call, self.generic_visit(node))
        if not self._is_request_security_call(node):
            return node
        if len(node.args) < 3:
            return node
        expr = node.args[2]
        if isinstance(expr, ast.Lambda):
            return node
        lambda_node = ast.Lambda(
            args=ast.arguments(
                posonlyargs=[],
                args=[],
                kwonlyargs=[],
                kw_defaults=[],
                defaults=[]
            ),
            body=expr
        )
        setattr(lambda_node, "_skip_function_isolation", True)
        ast.copy_location(lambda_node, expr)
        node.args[2] = lambda_node
        return node
