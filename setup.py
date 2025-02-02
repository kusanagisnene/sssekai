import sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setuptools, sssekai

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="sssekai",
    version=sssekai.__version__,
    author="greats3an",
    author_email="greats3an@gmail.com",
    description="Project SEKAI Asset Utility / PJSK 资源下载 + Live2D, Spine, USM 提取",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/mos9527/sssekai",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    install_requires=[
        "msgpack",
        "pycryptodome",
        "unitypy >= 1.20.10",
        "wannacri",
        "python-json-logger",
        "tqdm",
        "coloredlogs",
        "requests",
    ],
    entry_points={"console_scripts": ["sssekai = sssekai.__main__:__main__"]},
    python_requires=">=3.10",
)
