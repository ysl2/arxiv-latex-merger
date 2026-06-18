from setuptools import setup, find_packages

setup(
    name='arxiv-latex-merger',
    version='0.2.0',
    packages=find_packages(),
    install_requires=[
        'tqdm==4.65.0',
    ],
    entry_points={
        'console_scripts': [
            'arxiv-latex-merger = arxiv_latex_merger.cli:cli',
        ],
    },
)
