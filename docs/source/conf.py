# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information
import re
import pathlib


def get_version() -> str:
    version_string_path = pathlib.Path(__file__).parents[2] / "src/flexrag/__init__.py"
    with open(version_string_path) as f:
        version = re.search(r"__VERSION__ = \"(.*?)\"", f.read()).group(1)
    return version


project = "FlexRAG Documentation"
html_short_title = "FlexRAG Documentation"
copyright = "2025, ZhuochengZhang"
author = "ZhuochengZhang"
release = get_version()

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx_copybutton",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "sphinx_book_theme"
html_static_path = ["_static", "../../assets"]
html_theme_options = {
    "path_to_docs": "docs/source",
    "repository_url": "https://github.com/ictnlp/flexrag",
    "use_repository_button": True,
}
