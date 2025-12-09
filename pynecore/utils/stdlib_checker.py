import sys
import os
import importlib.util

from types import ModuleType
from importlib.machinery import ModuleSpec


class StdLibChecker:
    """Helper class to check if a module/function is part of Python's standard library"""

    def __init__(self):
        # Cache standard lib paths
        self.stdlib_paths: set[str] = set()

        # Get standard library paths from sys.stdlib_module_names
        if hasattr(sys, 'stdlib_module_names'):
            self.stdlib_modules = sys.stdlib_module_names
        else:
            # Fallback: build set from sys.modules
            self.stdlib_modules = {
                name for name, module in sys.modules.items()
                if self._is_stdlib_module(module)
            }

    def _is_stdlib_module(self, module: ModuleSpec | ModuleType) -> bool:
        """
        Check if a module is part of standard library based on its path
        """
        if isinstance(module, ModuleSpec):
            if not module.origin:
                return False
            path = module.origin
        else:
            if not hasattr(module, '__file__') or not module.__file__:
                return False
            path = module.__file__

        # Cache paths for better performance
        if not self.stdlib_paths:
            # Get Python's standard library path directly from sys.prefix
            stdlib_path = os.path.join(sys.prefix, 'Lib' if sys.platform == 'win32' else 'lib')
            if os.path.exists(stdlib_path):
                self.stdlib_paths.add(os.path.realpath(stdlib_path))

            # Also check sys.base_prefix for virtual environments
            base_stdlib_path = os.path.join(sys.base_prefix, 'Lib' if sys.platform == 'win32' else 'lib')
            if os.path.exists(base_stdlib_path):
                self.stdlib_paths.add(os.path.realpath(base_stdlib_path))

        # Check if module path is in stdlib paths and not in site-packages
        module_path = os.path.realpath(path)
        for stdlib_path in self.stdlib_paths:
            if module_path.startswith(stdlib_path):
                # Only exclude site-packages within stdlib path
                site_packages = os.path.join(stdlib_path, 'site-packages')
                if not module_path.startswith(site_packages):
                    return True
        return False

    def is_stdlib(self, module_name: str) -> bool:
        """
        Check if a module is part of Python's standard library.
        Also checks if referenced object is a builtin function/method.

        :param module_name: Full module path (e.g. 'os.path')
        :return: True if module is part of stdlib or object is builtin
        """
        # Get root module
        root_module = module_name.split('.')[0]

        # Quick check in stdlib_modules set
        if root_module in self.stdlib_modules:
            return True

        # Try to find spec
        try:
            spec = importlib.util.find_spec(root_module)
            if not spec or not spec.origin:
                return False

            # Check path
            return self._is_stdlib_module(spec)
        except (ImportError, AttributeError):
            return False


stdlib_checker = StdLibChecker()
