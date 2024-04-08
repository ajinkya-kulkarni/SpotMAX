import sys
import os
import argparse

import site
sitepackages = site.getsitepackages()
site_packages = [p for p in sitepackages if p.endswith('site-packages')][0]

cellacdc_path = os.path.dirname(os.path.abspath(__file__))
cellacdc_installation_path = os.path.dirname(cellacdc_path)
if cellacdc_installation_path != site_packages:
    # Running developer version. Delete cellacdc folder from site_packages 
    # if present from a previous installation of cellacdc from PyPi
    cellacdc_path_pypi = os.path.join(site_packages, 'cellacdc')
    if os.path.exists(cellacdc_path_pypi):
        import shutil
        try:
            shutil.rmtree(cellacdc_path_pypi)
        except Exception as err:
            print(err)
            print(
                '[ERROR]: Previous Cell-ACDC installation detected. '
                f'Please, manually delete this folder and re-start the software '
                f'"{cellacdc_path_pypi}". '
                'Thank you for you patience!'
            )
            exit()
        print('*'*60)
        input(
            '[WARNING]: SpotMAX had to clean-up and older installation. '
            'Please, re-start the software. Thank you for your patience! '
            '(Press any key to exit). '
        )
        exit()

spotmax_path = os.path.dirname(os.path.abspath(__file__))
spotmax_installation_path = os.path.dirname(spotmax_path)

if spotmax_installation_path != site_packages:
    # Running developer version. Delete spotmax folder from site_packages 
    # if present from a previous installation of spotmax from PyPi
    spotmax_path_pypi = os.path.join(site_packages, 'spotmax')
    if os.path.exists(spotmax_path_pypi):
        import shutil
        try:
            shutil.rmtree(spotmax_path_pypi)
        except Exception as err:
            print(err)
            print(
                '[ERROR]: Previous spotMAX installation detected. '
                f'Please, manually delete this folder and re-start the software '
                f'"{spotmax_path_pypi}". '
                'Thank you for you patience!'
            )
            exit()
        print('*'*60)
        input(
            '[WARNING]: SpotMAX had to clean-up and older installation. '
            'Please, re-start the software. Thank you for your patience! '
            '(Press any key to exit). '
        )
        exit()


from spotmax._run import run_gui, run_cli
from spotmax import help_text, GUI_INSTALLED

def cli_parser():
    ap = argparse.ArgumentParser(
        prog='spotMAX', description=help_text, 
        formatter_class=argparse.RawTextHelpFormatter
    )

    ap.add_argument(
        '-p', '--params',
        default='',
        type=str,
        metavar='PATH_TO_PARAMS',
        help=('Path of the ".ini" or "_analysis_inputs.csv" file')
    )

    ap.add_argument(
        '-m', '--metadata',
        default='',
        type=str,
        metavar='PATH_TO_METADATA_CSV',
        help=('Path of the "_metadata.csv" file')
    )

    ap.add_argument(
        '-g', '--log_folderpath',
        default='',
        type=str,
        metavar='LOG_FILE_FOLDERPATH',
        help=('Folder path where to save the log file (recommended when reporting an issue)')
    )

    ap.add_argument(
        '-t', '--report_folderpath',
        default='',
        type=str,
        metavar='REPORT_FOLDERPATH',
        help=('Folder path where to save the report created at the end of the analysis')
    )

    ap.add_argument(
        '-l', '--report_filename',
        default='',
        type=str,
        metavar='REPORT_FILENAME',
        help=('Filename of the report created at the end of the analysis')
    )

    ap.add_argument(
        '-e', '--disable_final_report',
        action='store_true',
        help=('Flag to disable the saving of a report at the end of the analysis.')
    )

    ap.add_argument(
        '-n', '--num_threads',
        default=0,
        type=int,
        metavar='NUMBA_NUM_THREADS',
        help=('Number of threads to use for parallel execution when using numba.')
    )

    ap.add_argument(
        '-f', '--force_default_values',
        action='store_true',
        help=('Flag to disable user inputs and use default values for missing parameters.')
    )

    ap.add_argument(
        '-v', '--reduce-verbosity',
        action='store_true',
        help=('Flag to reduce the amount of infromation logged and displayed in the terminal.')
    )

    ap.add_argument(
        '-o', '--output_tables_file_ext',
        default='.h5',
        type=str,
        metavar='OUTPUT_TABLES_FILE_EXT',
        help=('File extension of the output tables')
    )

    # NOTE: the user doesn't need to pass `-c`` because passing the path to the 
    # params is enough. However, passing `-c`` without path to params will 
    # raise an error with the explanation that the parameters file is 
    # mandatory in command line.
    ap.add_argument(
        '-c', '--cli',
        action='store_true',
        help=(
            'Flag to run spotMAX in the command line.'
            'Not required if you pass the `--params` argument.'
        )
    )

    ap.add_argument(
        '-r', '--raise_on_critical',
        action='store_true',
        help=(
            'Flag to force spotMAX to close upon critical error when running '
            'in batch mode.'
        )
    )

    ap.add_argument(
        '-u', '--gpu',
        action='store_true',
        help=(
            'Try using CUDA-compatible GPU. Requires `cupy` package.'
        )
    )

    ap.add_argument(
        '-d', '--debug',
        action='store_true',
        help=(
            'Used for debugging. Test code with '
            '"if self.debug: <debug code here>"'
        )
    )

    return vars(ap.parse_args())

def run():
    # print('Setting up required libraries...')
    from cellacdc._run import _install_tables
    requires_restart = _install_tables(parent_software='SpotMAX')
    if requires_restart:
        exit(
            '[NOTE]: SpotMAX had to install a required library and needs to be '
            'restarted. Thank you for you patience!. '
        )

    parser_args = cli_parser()

    PARAMS_PATH = parser_args['params']
    DEBUG = parser_args['debug']
    RUN_CLI = parser_args['cli']

    if RUN_CLI and not PARAMS_PATH:
        raise FileNotFoundError(
            '[ERROR]: To run spotMAX from the command line you need to '
            'provide a path to the "_analysis_inputs.ini" or '
            '"_analysis_inputs.csv" file. To run the GUI use the command '
            '`spotmax -g`'
        )

    if PARAMS_PATH:
        run_cli(parser_args, debug=DEBUG)
    else:
        run_gui(debug=DEBUG)

if __name__ == "__main__":
    run()
