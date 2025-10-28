from setuptools import setup, find_packages

setup(
    name="matryx",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        'aiohttp>=3.8.0',
        'python-dotenv>=0.19.0',
    ],
    python_requires='>=3.7',
)
