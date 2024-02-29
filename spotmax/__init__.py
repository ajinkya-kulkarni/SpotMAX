import os
import sys
import traceback
import warnings
import time
warnings.simplefilter(action='ignore', category=FutureWarning)

from functools import wraps

is_cli = True

try:
    from setuptools_scm import get_version
    __version__ = get_version(root='..', relative_to=__file__)
except Exception as e:
    try:
        from ._version import version as __version__
    except ImportError:
        __version__ = "not-installed"

__author__ = 'Francesco Padovani'

try:
    from cellacdc import gui as acdc_gui
    from qtpy.QtGui import QFont
    font = QFont()
    font.setPixelSize(11)
    font_small = QFont()
    font_small.setPixelSize(9)
    GUI_INSTALLED = True
except ModuleNotFoundError:
    GUI_INSTALLED = False
    
spotmax_path = os.path.dirname(os.path.abspath(__file__))
qrc_resources_path = os.path.join(spotmax_path, 'qrc_resources_spotmax.py')
resources_folderpath = os.path.join(spotmax_path, 'resources')

import inspect
from datetime import datetime
from pprint import pprint
import pathlib
import numpy as np

rng = np.random.default_rng(seed=6490)

spotMAX_path = os.path.dirname(spotmax_path)
html_path = os.path.join(spotmax_path, 'html_files')

home_path = pathlib.Path.home()
spotmax_appdata_path = os.path.join(home_path, 'spotmax_appdata')
unet_checkpoints_path = os.path.join(spotmax_appdata_path, 'unet_checkpoints')
unet2D_checkpoint_path = os.path.join(unet_checkpoints_path, 'unet2D')
unet3D_checkpoint_path = os.path.join(
    unet_checkpoints_path, 'unet3D', 'normal_30_250_250_20_100_100'
)
last_selection_meas_filepath = os.path.join(
    spotmax_appdata_path, 'last_selection_meas.ini'
)
last_used_ini_text_filepath = os.path.join(
    spotmax_appdata_path, 'last_used_ini_filepath.txt'
)
last_cli_log_file_path = os.path.join(
    spotmax_appdata_path, 'last_cli_log_file_path.txt'
)
data_path = os.path.join(spotMAX_path, 'data')

logs_path = os.path.join(spotmax_appdata_path, 'logs')
if not os.path.exists(logs_path):
    os.makedirs(logs_path)

settings_path = os.path.join(spotmax_appdata_path, 'settings')
if not os.path.exists(settings_path):
    os.makedirs(settings_path)

colorItems_path = os.path.join(settings_path, 'colorItems.json')
gui_settings_csv_path = os.path.join(settings_path, 'gui_settings.csv')

icon_path = os.path.join(resources_folderpath, 'spotMAX_icon.ico')
logo_path = os.path.join(resources_folderpath, 'spotMAX_logo.png')

from cellacdc import printl as acdc_printl
from cellacdc import base_cca_dict
def printl(*objects, **kwargs):
    acdc_printl(*objects, idx=2, **kwargs)

is_linux = sys.platform.startswith('linux')
is_mac = sys.platform == 'darwin'
is_win = sys.platform.startswith("win")
is_win64 = (is_win and (os.environ["PROCESSOR_ARCHITECTURE"] == "AMD64"))

issues_url = 'https://github.com/SchmollerLab/spotMAX/issues'

help_text = (
    'Welcome to spotMAX!\n\n'
    'You can run spotmax both as a GUI or in the command line.\n'
    'To run the GUI type `spotmax`. To run the command line type `spotmax -p <path_to_params_file>`.\n'
    'The `<path_to_params_file>` can either be a CSV or INI file.\n'
    'If you do not have one, use the GUI to set up the parameters.\n\n'
    'See below other arguments you can pass to the command line. Enjoy!'
)

CELL_SIZE_COLUMNS = [
    'cell_area_pxl',
    'cell_area_um2',
    'cell_vol_vox',
    'cell_vol_fl',
    'cell_vol_vox_3D',
    'cell_vol_fl_3D'
]

LT_DF_REQUIRED_COLUMNS = [
    'frame_i',
    'Cell_ID',
    'cell_cycle_stage',
    'relationship',
    'relative_ID'
]

error_up_str = '^'*100
error_up_str = f'\n{error_up_str}'
error_down_str = '^'*100
error_down_str = f'\n{error_down_str}'

ZYX_GLOBAL_COLS = ['z', 'y', 'x']
ZYX_LOCAL_COLS = ['z_local', 'y_local', 'x_local']
ZYX_AGGR_COLS = ['z_aggr', 'y_aggr', 'x_aggr']
ZYX_LOCAL_EXPANDED_COLS = [
    'z_local_expanded', 'y_local_expanded', 'x_local_expanded'
]
ZYX_FIT_COLS = ['z_fit', 'y_fit', 'x_fit']
ZYX_RESOL_COLS = ['z_resolution_pxl', 'y_resolution_pxl', 'x_resolution_pxl']


BASE_COLUMNS = ZYX_GLOBAL_COLS.copy()
BASE_COLUMNS.extend(ZYX_LOCAL_COLS)
BASE_COLUMNS.extend(ZYX_FIT_COLS)
BASE_COLUMNS.extend(ZYX_RESOL_COLS)
BASE_COLUMNS.extend(base_cca_dict.keys())
BASE_COLUMNS.extend(CELL_SIZE_COLUMNS)

DFs_FILENAMES = {
    'spots_detection': '*rn*_0_detected_spots*desc*',
    'spots_gop': '*rn*_1_valid_spots*desc*',
    'spots_spotfit': '*rn*_2_spotfit*desc*'
}

valid_true_bool_str = {
    'true', 'yes', 'on'
}
valid_false_bool_str = {
    'false', 'no', 'off'
}

def exception_handler_cli(func):
    @wraps(func)
    def inner_function(self, *args, **kwargs):
        try:
            if func.__code__.co_argcount==1 and func.__defaults__ is None:
                result = func(self)
            elif func.__code__.co_argcount>1 and func.__defaults__ is None:
                result = func(self, *args)
            else:
                result = func(self, *args, **kwargs)
        except Exception as e:
            result = None
            if self.is_cli:
                self.logger.exception(e)
            if not self.is_batch_mode:
                self.quit(error=e)
            else:
                raise e
        return result
    return inner_function

def handle_log_exception_cli(func):
    @wraps(func)
    def inner_function(self, *args, **kwargs):
        try:
            if func.__code__.co_argcount==1 and func.__defaults__ is None:
                result = func(self)
            elif func.__code__.co_argcount>1 and func.__defaults__ is None:
                result = func(self, *args)
            else:
                result = func(self, *args, **kwargs)
        except Exception as error:
            result = None
            self.log_exception_report(error, traceback.format_exc())
        return result
    return inner_function

def read_version():
    try:
        from setuptools_scm import get_version
        version = get_version(root='..', relative_to=__file__)
        return version
    except Exception as e:
        try:
            from . import _version
            return _version.version
        except Exception as e:
            return 'ND'

def njit_replacement(parallel=False):
    def wrap(func):
        def inner_function(*args, **kwargs):
            return func(*args, **kwargs)
        return inner_function
    return wrap