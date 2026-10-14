import ast


class ComparisonTransformer(ast.NodeTransformer):
    """Apply pairwise Pine tolerance without changing stored/plot values."""

    def __init__(self):
        self.used = set()

    @staticmethod
    def _literal_type(node):
        # Only syntax with a guaranteed builtin result; names, annotations and
        # function calls are not proof (they can be reassigned or shadowed).
        if isinstance(node, ast.Constant):
            return type(node.value)
        if isinstance(node, ast.JoinedStr):
            return str
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return bool
            if (isinstance(node.op, (ast.UAdd, ast.USub, ast.Invert))
                    and ComparisonTransformer._literal_type(node.operand) is int):
                return int
        if (isinstance(node, ast.BinOp)
                and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv,
                                         ast.Mod, ast.BitAnd, ast.BitOr, ast.BitXor,
                                         ast.LShift, ast.RShift))
                and ComparisonTransformer._literal_type(node.left) is int
                and ComparisonTransformer._literal_type(node.right) is int):
            return int
        return None

    def _operand(self, node):
        self.used.add('comparison_value')
        return ast.copy_location(ast.Call(
            func=ast.Name(id='__pyne_comparison_value__', ctx=ast.Load()),
            args=[node], keywords=[]), node)

    def visit_Compare(self, node):
        self.generic_visit(node)
        # Identity/membership are Python features, not Pine numeric comparisons.
        if any(isinstance(op, (ast.Is, ast.IsNot, ast.In, ast.NotIn)) for op in node.ops):
            return node
        if len(node.ops) == 1:
            left_type = self._literal_type(node.left)
            right_type = self._literal_type(node.comparators[0])
            # != still needs a helper when the unknown operand could be NA.
            nonnumeric = (str, bool, type(None))
            if (left_type is int and right_type is int
                    or (left_type in nonnumeric or right_type in nonnumeric)
                    and (not isinstance(node.ops[0], ast.NotEq)
                         or left_type is not None and right_type is not None)):
                return node
            name = {ast.Lt: 'lt', ast.LtE: 'le', ast.Gt: 'gt', ast.GtE: 'ge',
                    ast.Eq: 'eq', ast.NotEq: 'ne'}[type(node.ops[0])]
            self.used.add(name)
            return ast.copy_location(ast.Call(
                func=ast.Name(id=f'__pyne_compare_{name}__', ctx=ast.Load()),
                args=[node.left, node.comparators[0]], keywords=[]), node)
        # Every pair needs its own difference; rounding individual operands is
        # not equivalent. Wrappers keep native short-circuit/once-only semantics.
        node.left = self._operand(node.left)
        node.comparators = [self._operand(value) for value in node.comparators]
        return node

    def visit_Module(self, node):
        self.generic_visit(node)
        if self.used:
            index = 0
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                index = 1
            while (index < len(node.body) and isinstance(node.body[index], ast.ImportFrom)
                   and node.body[index].module == '__future__'):
                index += 1
            node.body.insert(index, ast.ImportFrom(
                module='pynecore.core.pine_comparison',
                names=[ast.alias(name=name, asname='__pyne_comparison_value__' if name == 'comparison_value'
                                 else f'__pyne_compare_{name}__') for name in sorted(self.used)], level=0))
        return node
