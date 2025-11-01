from setuptools import setup, find_packages

setup(
    name="matryx",
    version="0.2.0",
    author="Grise",
    description="A Python library for interacting with the Matrix API",
    url="https://github.com/Grise3/Matryx",
    packages=find_packages(),
    install_requires=[
        'aiohttp>=3.8.0',
        'python-dotenv>=0.19.0',
    ],
    extras_require={
        'images': ['Pillow>=8.0.0'],
        'datetime': ['pytz>=2021.1'],
    },
    python_requires='>=3.7',
    project_urls={
        'Bug Reports': 'https://github.com/Grise3/Matryx/issues',
        'Source': 'https://github.com/Grise3/Matryx',
    },
)
