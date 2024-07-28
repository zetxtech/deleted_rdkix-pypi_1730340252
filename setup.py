import os
import shlex
import sys
from distutils.file_util import copy_file
from pathlib import Path
from shutil import copytree, rmtree
from subprocess import call, check_call
from sysconfig import get_paths
from textwrap import dedent

from setuptools import Extension, find_packages, setup
from setuptools.command.build_ext import build_ext as build_ext_orig

# RDKix version to build (tag from github repository)
rdkix_tag = "Release_2023_03_2"

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()


class RDKix(Extension):
    def __init__(self, name, **kwargs):
        super().__init__(name, sources=[])
        self.__dict__.update(kwargs)


class BuildRDKix(build_ext_orig):
    def run(self):
        for ext in self.extensions:
            self.build_rdkix(ext)
        super().run()

    def get_ext_filename(self, ext_name):
        ext_path = ext_name.split(".")
        return os.path.join(*ext_path)

    def conan_install(self, conan_toolchain_path):
        """Run the Conan"""
        boost_version = "1.78.0"

        # This modified conanfile.py for boost does not link libpython*.so
        # When building a platform wheel, we don't want to link libpython*.so.
        mod_conan_path = "conan_boost_mod"

        # Export the modified boost version
        check_call(
            [
                "conan",
                "export",
                f"{mod_conan_path}/all/",
                f"{boost_version}@chris/mod_boost",
            ]
        )

        # needed for windows builds
        without_python_lib = "False"
        win = """eigen/3.4.0"""
        if sys.platform != "win32":
            win = ""
            without_python_lib = "True"

        without_stacktrace = "False"
        if "macosx_arm64" in os.environ["CIBW_BUILD"]:
            # does not work on macos arm64 for some reason
            without_stacktrace = "True"

        conanfile = f"""\
            [requires]
            boost/{boost_version}@chris/mod_boost
            {win}

            [generators]
            cmake_paths
            virtualrunenv

            [options]
            boost:shared=True
            boost:without_python=False
            boost:without_python_lib={without_python_lib}
            boost:python_executable={sys.executable}
            boost:without_stacktrace={without_stacktrace}
        """
        # boost:debug_level=1

        Path("conanfile.txt").write_text(dedent(conanfile))

        # run conan install
        cmd = [
            "conan",
            "install",
            "conanfile.txt",
            # build all missing
            "--build=missing",
            "-if",
            f"{conan_toolchain_path}",
        ]

        # but force build b2 on linux
        if "linux" in sys.platform:
            cmd += ["--build=b2"]

        # for arm 64 on MacOS
        if "macosx_arm64" in os.environ["CIBW_BUILD"]:
            cmd += ["-s", "arch=armv8", "-s", "arch_build=armv8"]

        check_call(cmd)

    def build_rdkix(self, ext):
        """Build RDKix

        Steps:
        (1) Use Conan to install boost and other libraries
        (2) Build RDKix
        (3) Copy RDKix and additional files to the wheel path
        (4) Copy the libraries to system paths
        """

        cwd = Path().absolute()

        # (1) Install boost and other libraries using Conan
        conan_toolchain_path = cwd / "conan"
        conan_toolchain_path.mkdir(parents=True, exist_ok=True)
        self.conan_install(conan_toolchain_path)

        # (2) Build RDkix
        # Define paths
        build_path = Path(self.build_temp).absolute()
        build_path.mkdir(parents=True, exist_ok=True)
        os.chdir(str(build_path))

        # Install path
        rdkix_install_path = build_path / "rdkix_install"
        rdkix_install_path.mkdir(parents=True, exist_ok=True)

        # Clone RDKix from git at rdkix_tag
        check_call(
            ["git", "clone", "-b", f"{ext.rdkix_tag}", "https://github.com/zetxtech/rdkix"]
        )

        # Location of license file
        license_file = build_path / "rdkix" / "license.txt"

        # Start build process
        os.chdir(str("rdkix"))

        if rdkix_tag == "Release_2023_03_2":
            # Cherry-pick https://github.com/zetxtech/rdkix/pull/6485/commits for
            # correct python install paths on windows 
            check_call(
                ["git", "config", "--global", "user.email", '"you@example.com"']
            )
            check_call(
                ["git", "config", "--global", "user.name", '"Your Name"']
            )
            check_call(
                ["git", "remote", "add", "upstream", "https://github.com/rdkit/rdkit.git"]
            )
            check_call(
                ["git", "fetch", "upstream", "pull/6485/head:fix_win_py_install"]
            )
            check_call(
                ["git", "cherry-pick", "91a1ce03424d2924acb5659561604ada9545bfb4"]
            )

        # Define CMake options
        options = [
            f"-DCMAKE_TOOLCHAIN_FILE={conan_toolchain_path / 'conan_paths.cmake'}",
            # Select correct python interpreter
            f"-DPYTHON_EXECUTABLE={sys.executable}",
            f"-DPYTHON_INCLUDE_DIR={get_paths()['include']}",
            # RDKix build flags
            "-DRDK_BUILD_INCHI_SUPPORT=ON",
            "-DRDK_BUILD_AVALON_SUPPORT=ON",
            "-DRDK_BUILD_PYTHON_WRAPPERS=ON",
            "-DRDK_BUILD_YAEHMOP_SUPPORT=ON",
            "-DRDK_BUILD_XYZ2MOL_SUPPORT=ON",
            "-DRDK_INSTALL_INTREE=OFF",
            "-DRDK_BUILD_CAIRO_SUPPORT=ON",
            "-DRDK_BUILD_FREESASA_SUPPORT=ON",
            # Disable system libs for finding boost
            "-DBoost_NO_SYSTEM_PATHS=ON",
            # build stuff
            f"-DCMAKE_INSTALL_PREFIX={rdkix_install_path}",
            "-DCMAKE_BUILD_TYPE=Release",
            # Speed up builds
            "-DRDK_BUILD_CPP_TESTS=OFF",
            # Fix InChi download
            "-DINCHI_URL=https://rdkit.org/downloads/INCHI-1-SRC.zip",
        ]

        # Modifications for Windows
        if sys.platform == "win32":
            # DRDK_INSTALL_STATIC_LIBS should be fixed in newer RDKix builds
            options += [
                "-Ax64",
                "-DRDK_INSTALL_STATIC_LIBS=OFF",
                "-DRDK_INSTALL_DLLS_MSVC=ON",
            ]

            def to_win_path(pt: Path):
                return str(pt).replace("\\", "/")

            # Link cairo
            vcpkg_path = Path("C:/vcpkg")
            vcpkg_inc = vcpkg_path / "installed" / "x64-windows" / "include"
            vcpkg_lib = vcpkg_path / "installed" / "x64-windows" / "lib"
            options += [
                f"-DCAIRO_INCLUDE_DIR={to_win_path(vcpkg_inc)}",
                f"-DCAIRO_LIBRARY_DIR={to_win_path(vcpkg_lib)}",
                f"-DFREETYPE_INCLUDE_DIRS={to_win_path(vcpkg_inc)}",
                f"-DFREETYPE_LIBRARY={to_win_path(vcpkg_lib / 'freetype.lib')}",
            ]

        # Modifications for MacOS
        if sys.platform == "darwin":
            options += [
                "-DCMAKE_C_FLAGS=-Wno-implicit-function-declaration",
                # CATCH_CONFIG_NO_CPP17_UNCAUGHT_EXCEPTIONS because MacOS does not fully support C++17.
                '-DCMAKE_CXX_FLAGS="-Wno-implicit-function-declaration -DCATCH_CONFIG_NO_CPP17_UNCAUGHT_EXCEPTIONS"',
            ]

        # Modifications for MacOS arm64 (M1 hardware)
        vars = {}
        if "macosx_arm64" in os.environ["CIBW_BUILD"]:
            options += [
                "-DCMAKE_OSX_ARCHITECTURES=arm64",
                "-DRDK_OPTIMIZE_POPCNT=OFF",
            ]
            # also export it to compile yaehmop for arm64 too
            vars["CMAKE_OSX_ARCHITECTURES"] = "arm64"

        cmds = [
            # f"cmake -S . -B build {' '.join(options)}",
            f"cmake -S . -B build {' '.join(options)} ",
            # f"cmake --build build"
            # if sys.platform != "win32"
            "cmake --build build -j 4 --config Release",
            "cmake --install build",
        ]

        print('!!! --- CMAKE build command', file=sys.stderr)
        print(cmds, file=sys.stderr)

        # Run CMake and install RDKix
        [
            check_call(
                shlex.split(c, posix="win32" not in sys.platform),
                env=dict(os.environ, **vars),
            )
            for c in cmds
        ]

        os.chdir(str(cwd))

        # (3) Copy RDKix and additional files to the wheel path
        py_name = "python" + ".".join(map(str, sys.version_info[:2]))
        rdkix_files = rdkix_install_path / "lib" / py_name / "site-packages" / "rdkix"

        if sys.platform == "win32":
            rdkix_files = rdkix_install_path / "Lib" / "site-packages" / "rdkix"

        # Modify RDPaths.py
        sed = "gsed" if sys.platform == "darwin" else "sed"
        call(
            [
                sed,
                "-i",
                "/_share =/c\_share = os.path.dirname(__file__)",  # noqa: W605
                f"{rdkix_files / 'RDPaths.py'}",
            ]
        )

        # Data directory
        rdkix_data_path = rdkix_install_path / "share" / "RDKix" / "Data"

        # Contrib directory
        rdkix_contrib_path = rdkix_install_path / "share" / "RDKix" / "Contrib"

        # copy rdkix files here, make sure it's empty
        wheel_path = Path(self.get_ext_fullpath(ext.name)).absolute()
        if wheel_path.exists():
            rmtree(str(wheel_path))

        # Copy the Python files
        copytree(str(rdkix_files), str(wheel_path))

        # Copy the data directory
        copytree(str(rdkix_data_path), str(wheel_path / "Data"))

        # Copy the contrib directory
        copytree(str(rdkix_contrib_path), str(wheel_path / "Contrib"))

        # Delete some large files from the Contrib folder
        # that are not necessary for running RDKix
        # See https://github.com/rdkit/rdkit/issues/5601
        _dir = wheel_path / "Contrib" / "NIBRSubstructureFilters"
        rmtree(str(_dir / "examples"))
        (_dir / "FilterSet_NIBR2019_wPubChemExamples.html").unlink()
        (_dir / "filterExamples.png").unlink()

        _dir = wheel_path / "Contrib" / "CalcLigRMSD"
        rmtree(str(_dir / "data"))
        rmtree(str(_dir / "figures"))
        (_dir / "Examples_CalcLigRMSD.ipynb").unlink()

        # Copy the license
        copy_file(str(license_file), str(wheel_path))

        # (4) Copy the libraries to system paths
        rdkix_root = rdkix_install_path / "lib"

        if "linux" in sys.platform:
            to_path = Path("/usr/local/lib")
            [copy_file(i, str(to_path)) for i in rdkix_root.glob("*.so*")]
        elif "win32" in sys.platform:
            # windows is Lib or libs?
            to_path = Path("C://libs")
            to_path.mkdir(parents=True, exist_ok=True)
            [copy_file(i, str(to_path)) for i in rdkix_root.glob("*.dll")]

        elif "darwin" in sys.platform:
            to_path = Path("/usr/local/lib")
            [copy_file(i, str(to_path)) for i in rdkix_root.glob("*dylib")]


setup(
    name="rdkix",
    version=rdkix_tag.replace("Release_", "").replace("_", "."),
    description="A collection of chemoinformatics and machine-learning software written in C++ and Python",
    author="Christopher Kuenneth",
    author_email="chris@kuenneth.dev",
    url="https://github.com/kuelumbus/rdkix",
    project_urls={
        "RDKix": "http://rdkit.org/",
        "RDKix on Github": "https://github.com/zetxtech/rdkix",
    },
    license="BSD-3-Clause",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    install_requires=[
        "numpy",
        "Pillow",
    ],
    ext_modules=[
        RDKix("rdkix", rdkix_tag=rdkix_tag),
    ],
    cmdclass=dict(build_ext=BuildRDKix),
)
