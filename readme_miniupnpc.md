MiniUPNPc module is included as part of TeleNVDA to forward ports and ease the creation of a direct connection server. Due to the complexity of the building process, prebuilt binaries are available on the add-on source. This document explains how to build them yourself in case you want to recreate the process. If you find a way to automate it while building the add-on, feel free to open a pull request.

## Requirements

You will need the following components:

* Python, versions 3.7 and 3.11 for x86, with setuptools package installed.
* Visual Studio 2022 with the latest Windows SDK and MSVC toolchain for x86 installed.
* CMake. You can install it as a Visual Studio component.
* Git for Windows or similar.

## Steps

1. Clone MiniUPNP Git repository: `git clone https://github.com/miniupnp/miniupnp.git`
2. Checkout the tag for the latest release. For example: `git checkout miniupnpd_2_3_9`
3. Open the 'x86 Native Tools Command Prompt for VS 2022' and navigate to the 'miniupnpc' subfolder inside the cloned repository.
4. Generate solution and project files compatible with vs2022. They will be written to the 'output' folder: `cmake -B output -G "Visual Studio 17 2022" -A Win32`
5. Build the projects in release mode: `cmake --build output --config Release`
6. Copy output/release/libminiupnpc.lib to the current directory, renaming it as miniupnpc.lib: `copy output\Release\libminiupnpc.lib miniupnpc.lib`
7. Build a static Python 3.11 module which won't require extra dlls to work: `py -3.11-32 setupmingw32.py build`
8. Do the same on Python 3.7: `py -3.7-32 setupmingw32.py build`
9. Copy the generated files to the add-on source. They can be found on build/lib.win32-cpython-311 and build/lib.win32-3.7, respectively.
10. That's all, enjoy! Did you expect more steps?
