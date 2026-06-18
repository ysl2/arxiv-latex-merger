from setuptools import setup, find_packages

setup(
    name='arxiv-latex-merger',
    version='0.2.0',
    packages=find_packages(),
    install_requires=[
        'arxiv==1.3.0',
        'feedparser',
    ],
    entry_points={
        'console_scripts': [
            'arxiv-latex-merger = arxiv_latex_merger.cli:cli',
        ],
    },
)
