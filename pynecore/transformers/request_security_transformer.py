import ast
from typing import cast


class RequestSecurityTransformer(ast.NodeTransformer):
    """
    Wrap the 3rd argument of request.security(...) in a zero-arg lambda for lazy evaluation.
    """
    def __init__(self):
        super().__init__()
        self._found = False

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
        self._found = True
        expression_keyword = next((kw for kw in node.keywords if kw.arg == "expression"), None)
        if len(node.args) < 3 and expression_keyword is None:
            return node
        expr = node.args[2] if len(node.args) >= 3 else expression_keyword.value
        if isinstance(expr, ast.Lambda):
            setattr(expr, "_security_expression", True)
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
        setattr(lambda_node, "_security_expression", True)
        ast.copy_location(lambda_node, expr)
        if len(node.args) >= 3:
            node.args[2] = lambda_node
        else:
            expression_keyword.value = lambda_node
        return node

    def visit_Module(self, node: ast.Module) -> ast.AST:
        node = cast(ast.Module, self.generic_visit(node))
        if not self._found:
            return node
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "__has_request_security__":
                        return node
        assign = ast.Assign(
            targets=[ast.Name(id="__has_request_security__", ctx=ast.Store())],
            value=ast.Constant(True),
        )
        ast.fix_missing_locations(assign)
        insert_at = 1 if (node.body and isinstance(node.body[0], ast.Expr)
                          and isinstance(cast(ast.Expr, node.body[0]).value, ast.Constant)
                          and isinstance(cast(ast.Constant, cast(ast.Expr, node.body[0]).value).value, str)) else 0
        node.body.insert(insert_at, assign)
        return node
