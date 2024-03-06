import os
import datetime
import re
import pathlib
import time
import shutil
import tempfile
import traceback
from pprint import pprint
from functools import partial

import numpy as np
import pandas as pd
from natsort import natsorted

from collections import defaultdict

from qtpy import QtCore
from qtpy.QtCore import Qt, Signal, QEventLoop, QPointF, QTimer
from qtpy.QtGui import (
    QFont, QFontMetrics, QTextDocument, QPalette, QColor,
    QIcon
)
from qtpy.QtWidgets import (
    QDialog, QComboBox, QVBoxLayout, QHBoxLayout, QLabel, QApplication,
    QPushButton, QPlainTextEdit, QCheckBox, QTreeWidget, QTreeWidgetItem,
    QTreeWidgetItemIterator, QAbstractItemView, QFrame, QFormLayout,
    QMainWindow, QWidget, QTableView, QTextEdit, QGridLayout,
    QSpacerItem, QSpinBox, QDoubleSpinBox, QButtonGroup, QGroupBox,
    QFileDialog, QDockWidget, QTabWidget, QScrollArea, QScrollBar
)

import pyqtgraph as pg

from cellacdc import apps as acdc_apps
from cellacdc import widgets as acdc_widgets
from cellacdc import myutils as acdc_myutils
from cellacdc import html_utils as acdc_html

from . import html_func, io, widgets, utils, config
from . import core, features
from . import printl, font
from . import tune, docs
from . import gui_settings_csv_path as settings_csv_path
from . import last_selection_meas_filepath
from . import palettes

class QBaseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

    def show(self, block=False):
        self.setWindowFlags(Qt.Dialog | Qt.WindowStaysOnTopHint)
        super().show()
        if block:
            self.loop = QEventLoop()
            self.loop.exec_()

    def exec_(self):
        self.show(block=True)

    def closeEvent(self, event):
        if hasattr(self, 'loop'):
            self.loop.exit()

class GopFeaturesAndThresholdsDialog(QBaseDialog):
    def __init__(self, parent=None, category='spots'):
        self.cancel = True
        super().__init__(parent)

        self.setWindowTitle(
            f'Features and thresholds for filtering valid {category}')

        mainLayout = QVBoxLayout()

        self.setFeaturesGroupbox = widgets.GopFeaturesAndThresholdsGroupbox(
            category=category
        )
        mainLayout.addWidget(self.setFeaturesGroupbox)
        mainLayout.addStretch(1)

        buttonsLayout = acdc_widgets.CancelOkButtonsLayout()
        buttonsLayout.cancelButton.clicked.connect(self.close)
        buttonsLayout.okButton.clicked.connect(self.ok_cb)

        mainLayout.addLayout(buttonsLayout)

        self.setLayout(mainLayout)
    
    def show(self, block=False) -> None:
        super().show(block=False)
        firstButton = self.setFeaturesGroupbox.selectors[0].selectButton
        featuresNeverSet = firstButton.text().find('Click') != -1
        if featuresNeverSet:
            self.setFeaturesGroupbox.selectors[0].selectButton.click()
        super().show(block=block)
    
    def configIniParam(self):
        paramsText = ''
        for selector in self.setFeaturesGroupbox.selectors:
            selectButton = selector.selectButton
            column_name = selectButton.toolTip()
            if not column_name:
                continue
            lowValue = selector.lowRangeWidgets.value()
            highValue = selector.highRangeWidgets.value()
            if lowValue is None and highValue is None:
                self.warnRangeNotSelected(selectButton.text())
                return False
            paramsText = f'{paramsText}  * {column_name}, {lowValue}, {highValue}\n'
        tooltip = f'Features and ranges set:\n\n{paramsText}'
        return tooltip
    
    def warnRangeNotSelected(self, buttonText):
        msg = acdc_widgets.myMessageBox(wrapText=False)
        txt = html_func.paragraph(
            'The following feature<br><br>'
            f'<code>{buttonText}</code><br><br>'
            'does <b>not have a valid range</b>.<br><br>'
            'Make sure you select <b>at least one</b> of the lower and higher '
            'range values.'
        )
        msg.critical(self, 'Invalid selection', txt)
    
    def ok_cb(self):
        isSelectionValid = self.configIniParam()
        if not isSelectionValid:
            return
        self.cancel = False
        self.close()


class measurementsQGroupBox(QGroupBox):
    def __init__(self, names, parent=None):
        QGroupBox.__init__(self, 'Single cell measurements', parent)
        self.formWidgets = []

        self.setCheckable(True)
        layout = widgets.FormLayout()

        for row, item in enumerate(names.items()):
            key, labelTextRight = item
            widget = widgets.formWidget(
                QCheckBox(), labelTextRight=labelTextRight,
                parent=self, key=key
            )
            layout.addFormWidget(widget, row=row)
            self.formWidgets.append(widget)

        row += 1
        buttonsLayout = QHBoxLayout()
        self.selectAllButton = QPushButton('Deselect all', self)
        self.selectAllButton.setCheckable(True)
        self.selectAllButton.setChecked(True)
        helpButton = widgets.acdc_widgets.helpPushButton('Help', self)
        buttonsLayout.addStretch(1)
        buttonsLayout.addWidget(self.selectAllButton)
        buttonsLayout.addWidget(helpButton)
        layout.addLayout(buttonsLayout, row, 0, 1, 4)

        row += 1
        layout.setRowStretch(row, 1)
        layout.setColumnStretch(3, 1)

        layout.setVerticalSpacing(10)
        self.setFont(widget.labelRight.font())
        self.setLayout(layout)

        self.toggled.connect(self.checkAll)
        self.selectAllButton.clicked.connect(self.checkAll)

        for _formWidget in self.formWidgets:
            _formWidget.widget.setChecked(True)

    def checkAll(self, isChecked):
        for _formWidget in self.formWidgets:
            _formWidget.widget.setChecked(isChecked)
        if isChecked:
            self.selectAllButton.setText('Deselect all')
        else:
            self.selectAllButton.setText('Select all')

class guiQuickSettingsGroupbox(QGroupBox):
    sigPxModeToggled = Signal(bool, bool)
    sigChangeFontSize = Signal(int)

    def __init__(self, df_settings, parent=None):
        super().__init__(parent)
        self.setTitle('Quick settings')

        formLayout = QFormLayout()
        formLayout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        formLayout.setFormAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.autoSaveToggle = acdc_widgets.Toggle()
        autoSaveTooltip = (
            'Automatically store a copy of the segmentation data and of '
            'the annotations in the `.recovery` folder after every edit.'
        )
        self.autoSaveToggle.setChecked(True)
        self.autoSaveToggle.setToolTip(autoSaveTooltip)
        autoSaveLabel = QLabel('Autosave')
        autoSaveLabel.setToolTip(autoSaveTooltip)
        formLayout.addRow(autoSaveLabel, self.autoSaveToggle)

        self.highLowResToggle = acdc_widgets.Toggle()
        self.highLowResToggle.setShortcut('w')
        highLowResTooltip = (
            'Resolution of the text annotations. High resolution results '
            'in slower update of the annotations.\n'
            'Not recommended with a number of segmented objects > 500.\n\n'
            'SHORTCUT: "W" key'
        )
        highResLabel = QLabel('High resolution')
        highResLabel.setToolTip(highLowResTooltip)
        self.highLowResToggle.setToolTip(highLowResTooltip)
        formLayout.addRow(highResLabel, self.highLowResToggle)

        self.realTimeTrackingToggle = acdc_widgets.Toggle()
        self.realTimeTrackingToggle.setChecked(True)
        self.realTimeTrackingToggle.setDisabled(True)
        label = QLabel('Real-time tracking')
        label.setDisabled(True)
        self.realTimeTrackingToggle.label = label
        formLayout.addRow(label, self.realTimeTrackingToggle)

        self.pxModeToggle = acdc_widgets.Toggle()
        self.pxModeToggle.setChecked(True)
        pxModeTooltip = (
            'With "Pixel mode" active, the text annotations scales relative '
            'to the object when zooming in/out (fixed size in pixels).\n'
            'This is typically faster to render, but it makes annotations '
            'smaller/larger when zooming in/out, respectively.\n\n'
            'Try activating it to speed up the annotation of many objects '
            'in high resolution mode.\n\n'
            'After activating it, you might need to increase the font size '
            'from the menu on the top menubar `Edit --> Font size`.'
        )
        pxModeLabel = QLabel('Pixel mode')
        self.pxModeToggle.label = pxModeLabel
        pxModeLabel.setToolTip(pxModeTooltip)
        self.pxModeToggle.setToolTip(pxModeTooltip)
        self.pxModeToggle.clicked.connect(self.pxModeToggled)
        formLayout.addRow(pxModeLabel, self.pxModeToggle)

        # Font size
        self.fontSizeSpinBox = acdc_widgets.SpinBox()
        self.fontSizeSpinBox.setMinimum(1)
        self.fontSizeSpinBox.setMaximum(99)
        formLayout.addRow('Font size', self.fontSizeSpinBox) 
        savedFontSize = str(df_settings.at['fontSize', 'value'])
        if savedFontSize.find('pt') != -1:
            savedFontSize = savedFontSize[:-2]
        self.fontSize = int(savedFontSize)
        if 'pxMode' not in df_settings.index:
            # Users before introduction of pxMode had pxMode=False, but now 
            # the new default is True. This requires larger font size.
            self.fontSize = 2*self.fontSize
            df_settings.at['pxMode', 'value'] = 1
            df_settings.to_csv(settings_csv_path)

        self.fontSizeSpinBox.setValue(self.fontSize)
        self.fontSizeSpinBox.editingFinished.connect(self.changeFontSize) 
        self.fontSizeSpinBox.sigUpClicked.connect(self.changeFontSize)
        self.fontSizeSpinBox.sigDownClicked.connect(self.changeFontSize)

        formLayout.addWidget(self.quickSettingsGroupbox)
        formLayout.addStretch(1)

        self.setLayout(formLayout)
    
    def pxModeToggled(self, checked):
        self.sigPxModeToggled.emit(checked, self.highLowResToggle.isChecked())
    
    def changeFontSize(self):
        self.sigChangeFontSize.emit(self.fontSizeSpinBox.value())

class guiTabControl(QTabWidget):
    sigRunAnalysis = Signal(str, bool)
    sigSetMeasurements = Signal()
    sigParametersLoaded = Signal(str)

    def __init__(self, parent=None, logging_func=print):
        super().__init__(parent)

        self.loadedFilename = ''
        self.lastEntry = None
        self.lastSavedIniFilePath = ''

        self.parametersTab = QScrollArea(self)
        self.parametersTab.setWidgetResizable(True)
        self.parametersQGBox = ParamsGroupBox(
            parent=self.parametersTab, 
            debug=True,
            logging_func=logging_func
        )
        self.parametersTab.setWidget(self.parametersQGBox)        
        
        self.logging_func = logging_func
        containerWidget = QWidget()
        containerLayout = QVBoxLayout()

        buttonsLayout = QHBoxLayout()
        
        self.saveParamsButton = acdc_widgets.savePushButton(
            'Save parameters to file...'
        )
        self.loadPreviousParamsButton = acdc_widgets.browseFileButton(
            'Load parameters from previous analysis...', 
            ext={'Configuration files': ['.ini', '.csv']},
            start_dir=acdc_myutils.getMostRecentPath(), 
            title='Select analysis parameters file'
        )
        buttonsLayout.addWidget(self.loadPreviousParamsButton)
        buttonsLayout.addWidget(self.saveParamsButton)
        buttonsLayout.addStretch(1)

        self.runSpotMaxButton = widgets.RunSpotMaxButton('  Run analysis...')
        buttonsLayout.addWidget(self.runSpotMaxButton)
        
        buttonsBottomLayout = QHBoxLayout()
        
        self.setMeasurementsButton = acdc_widgets.setPushButton(
            'Set measurements to save...'
        )
        buttonsBottomLayout.addWidget(self.setMeasurementsButton)
        buttonsBottomLayout.addStretch(1)

        containerLayout.addLayout(buttonsLayout)
        containerLayout.addWidget(self.parametersTab)
        containerLayout.addLayout(buttonsBottomLayout)
        
        containerWidget.setLayout(containerLayout)

        self.loadPreviousParamsButton.sigPathSelected.connect(
            self.loadPreviousParams
        )
        self.saveParamsButton.clicked.connect(self.saveParamsFile)
        self.runSpotMaxButton.clicked.connect(self.runAnalysis)
        self.setMeasurementsButton.clicked.connect(self.setMeasurementsClicked)

        self.addTab(containerWidget, 'Analysis parameters')
    
    def confirmMeasurementsSet(self):
        self.setMeasurementsButton.setText('Measurements are set. View or edit...')
        QTimer.singleShot(100, self.setMeasurementsButton.confirmAction)
    
    def runAnalysis(self):
        txt = html_func.paragraph("""
            Do you want to <b>save the current parameters</b> 
            to a configuration file?<br><br>
            A configuration file can be used to run the analysis again with 
            same parameters.
        """)
        msg = acdc_widgets.myMessageBox()
        _, yesButton, noButton = msg.question(
            self, 'Save parameters?', txt, 
            buttonsTexts=('Cancel', 'Yes', 'No')
        )
        if msg.cancel:
            return
        if msg.clickedButton == yesButton:
            ini_filepath = self.saveParamsFile()
            if not ini_filepath:
                return
            is_tempinifile = False
        else:
            # Save temp ini file
            temp_dirpath = tempfile.mkdtemp()
            now = datetime.datetime.now().strftime(r'%Y-%m-%d_%H-%M-%S')
            ini_filename = f'{now}_spotmax_analysis_parameters.ini'
            ini_filepath = os.path.join(temp_dirpath, ini_filename)
            self.parametersQGBox.saveToIniFile(ini_filepath)
            is_tempinifile = True
            if self.lastSavedIniFilePath:
                with open(self.lastSavedIniFilePath, 'r') as ini:
                    saved_ini_text = ini.read()
                with open(ini_filepath, 'r') as ini_temp:
                    temp_ini_text = ini_temp.read()
                if saved_ini_text == temp_ini_text:
                    ini_filepath = self.lastSavedIniFilePath
                    is_tempinifile = False 
        
        ini_filepath = ini_filepath.replace('\\', os.sep)
        ini_filepath = ini_filepath.replace('/', os.sep)
        txt = html_func.paragraph(f"""
            spotMAX analysis will now <b>run in the terminal</b>. All progress 
            will be displayed there.<br><br>
            Make sure to <b>keep an eye on the terminal</b> since it might require 
            your attention.<br><br>
            
            NOTE: If you prefer to run this analysis manually in any terminal of 
            your choice run the following command:<br>
        """)
        msg = acdc_widgets.myMessageBox()
        msg.information(
            self, 'Analysis will run in the terminal', txt,
            buttonsTexts=('Cancel', 'Ok, run now!'),
            commands=(f'spotmax -p "{ini_filepath}"',)
        )
        if msg.cancel:
            try:
                shutil.rmtree(temp_dirpath)
            except Exception as e:
                pass
            return

        self.sigRunAnalysis.emit(ini_filepath, is_tempinifile)
    
    def initState(self, isDataLoaded):
        self.isDataLoaded = isDataLoaded
        self.autoTuneTabWidget.autoTuneGroupbox.setDisabled(not isDataLoaded)
        if isDataLoaded:
            self.autoTuneTabWidget.autoTuningButton.clicked.disconnect()
        else:
            self.autoTuneTabWidget.autoTuningButton.clicked.connect(
                self.warnDataNotLoadedYet
            )
        if isDataLoaded:
            self.autoTuneTabWidget.addAutoTunePointsButton.clicked.disconnect()
        else:
            self.autoTuneTabWidget.addAutoTunePointsButton.clicked.connect(
                self.warnDataNotLoadedYet
            )

    def warnDataNotLoadedYet(self):
        txt = html_func.paragraph("""
            Before computing any of the analysis steps you need to <b>load some 
            image data</b>.<br><br>
            To do so, click on the <code>Open folder</code> button on the left of 
            the top toolbar (Ctrl+O) and choose an experiment folder to load. 
        """)
        msg = acdc_widgets.myMessageBox()
        msg.warning(self, 'Data not loaded', txt)
        self.sender().setChecked(False)
    
    def setValuesFromParams(self, params):
        # Check if we need to add new widgets for sections with addFieldButton
        for section, section_options in params.items():
            for anchor, options in section_options.items():
                if not options.get('addAddFieldButton', False):
                    continue
                
                splitted = anchor.split('_')
                if len(splitted) == 1:
                    continue
                
                parentAnchor = splitted[0]
                fieldIdx = splitted[-1]
                try:
                    fieldIdx = int(fieldIdx)
                except Exception as err:
                    continue
                
                widget_options = (
                    self.parametersQGBox.params[section][parentAnchor]
                )
                formWidget = widget_options['formWidget']                
                formWidget.addField()
        
        for section, anchorOptions in self.parametersQGBox.params.items():
            for anchor, options in anchorOptions.items():
                formWidget = options['formWidget']
                try:
                    val = params[section][anchor]['loadedVal']
                except Exception as e:
                    continue
                groupbox = options['groupBox']
                try:
                    groupbox.setChecked(True)
                except Exception as e:
                    pass
                # printl(section, anchor, val)
                valueSetter = params[section][anchor].get('valueSetter')
                formWidget.setValue(val, valueSetter=valueSetter)
                if formWidget.useEditableLabel:
                    formWidget.labelLeft.setValue(
                        params[section][anchor]['desc']
                    )
        
        self.parametersQGBox.updateMinSpotSize()
        spotsParams = self.parametersQGBox.params['Spots channel']
        spotPredMethodWidget = spotsParams['spotPredictionMethod']['widget']
        spotPredMethodWidget.nnet_params_from_ini_sections(params)
        spotPredMethodWidget.bioimageio_params_from_ini_sections(params)
    
    def validateIniFile(self, filePath):
        params = config.getDefaultParams()
        with open(filePath, 'r') as file:
            txt = file.read()
        isAnySectionPresent = any(
            [txt.find(f'[{section}]') != -1 for section in params.keys()]
        )
        if isAnySectionPresent:
            return True
        
        msg = acdc_widgets.myMessageBox(wrapText=False)
        txt = html_func.paragraph(""" 
            The loaded INI file does <b>not contain any valid section</b>.<br><br>
            Please double-check that you are loading the correct file.<br><br>
            Loaded file:
        """)
        msg.warning(
            self, 'Invalid INI file', txt, 
            commands=(filePath,), 
            path_to_browse=os.path.dirname(filePath)
        )
        
        return False
    
    def removeAddedFields(self):
        sections = list(self.parametersQGBox.params.keys())
        for section in sections:
            anchorOptions = self.parametersQGBox.params[section]
            anchors = list(anchorOptions.keys())
            for anchor in anchors:
                formWidget = anchorOptions[anchor]['formWidget']
                if hasattr(formWidget, 'delFieldButton'):
                    formWidget.delFieldButton.click()
    
    def loadPreviousParams(self, filePath):
        self.logging_func(f'Loading analysis parameters from "{filePath}"...')
        acdc_myutils.addToRecentPaths(os.path.dirname(filePath))
        self.loadedFilename, ext = os.path.splitext(os.path.basename(filePath))
        proceed = self.validateIniFile(filePath)
        if not proceed:
            return
        self.removeAddedFields()
        params = config.analysisInputsParams(filePath, cast_dtypes=False)
        self.setValuesFromParams(params)
        self.parametersQGBox.setSelectedMeasurements(filePath)
        self.showParamsLoadedMessageBox()
        self.sigParametersLoaded.emit(filePath)
        if self.parametersQGBox.selectedMeasurements is None:
            QTimer.singleShot(100, self.loadPreviousParamsButton.confirmAction)
            return
        self.confirmMeasurementsSet()
        QTimer.singleShot(100, self.loadPreviousParamsButton.confirmAction)
    
    def showParamsLoadedMessageBox(self):
        msg = acdc_widgets.myMessageBox(wrapText=False)
        txt = html_func.paragraph("""
            Parameters loaded!<br>
        """)
        msg.information(self, 'Parameters loaded', txt)
    
    def askSetMeasurements(self):
        if self.setMeasurementsButton.text().find('are set.') != -1:
            msg_type = 'warning'
            txt = html_func.paragraph(
                'There are <b>measurements that have previously set</b> '
                'and will be saved along with the parameters.<br><br>'
                'Do you want to edit or view which <b>measurements will be '
                'saved</b>?<br>'
            )
            noText = 'No, save the set measurements'
        else:
            msg_type = 'question'
            txt = html_func.paragraph(
                'Do you want to select which <b>measurements to save?</b><br>'
            )
            noText = 'No, save all the measurements'

        msg = acdc_widgets.myMessageBox(wrapText=False)
        _, noButton, yesButton = getattr(msg, msg_type)(
            self, 'Set measurements?', txt, 
            buttonsTexts=('Cancel', noText, 'Yes, view set measurments.')
        )
        return msg.clickedButton == yesButton, msg.cancel
    
    def setMeasurementsClicked(self):
        parametersGroupBox = self.parametersQGBox
        
        spotsParams = parametersGroupBox.params['Spots channel']
        anchor = 'doSpotFit'
        isSpotFitRequested = spotsParams[anchor]['widget'].isChecked()
        
        win = SetMeasurementsDialog(
            parent=self, 
            selectedMeasurements=parametersGroupBox.selectedMeasurements,
            isSpotFitRequested=isSpotFitRequested
        )
        win.sigOk.connect(self.setSpotmaxMeasurements)
        win.exec_()
        return win.cancel
    
    def setSpotmaxMeasurements(self, selectedMeasurements):
        self.parametersQGBox.selectedMeasurements = selectedMeasurements
        self.confirmMeasurementsSet()
    
    def saveParamsFile(self):
        showSetMeas, cancel = self.askSetMeasurements()
        if cancel:
            return ''
        
        if showSetMeas:
            cancel = self.setMeasurementsClicked()
            if cancel:
                return ''
        
        if self.loadedFilename:
            entry = self.loadedFilename
        elif self.lastEntry is not None:
            entry = self.lastEntry
        else:
            now = datetime.datetime.now().strftime(r'%Y-%m-%d')
            entry = f'{now}_analysis_parameters'
        txt = (
            'Insert <b>filename</b> for the parameters file.<br><br>'
            'After confirming, you will be asked to <b>choose the folder</b> '
            'where to save the file.'
        )
        while True:
            filenameWindow = acdc_apps.filenameDialog(
                parent=self, title='Insert file name for the parameters file', 
                allowEmpty=False, defaultEntry=entry, ext='.ini', hintText=txt
            )
            filenameWindow.exec_()
            if filenameWindow.cancel:
                return ''
            
            self.lastEntry = filenameWindow.entryText
            
            folder_path = QFileDialog.getExistingDirectory(
                self, 'Select folder where to save the parameters file', 
                acdc_myutils.getMostRecentPath()
            )
            if not folder_path:
                return ''
            
            filePath = os.path.join(folder_path, filenameWindow.filename)
            if not os.path.exists(filePath):
                break
            else:
                msg = acdc_widgets.myMessageBox(wrapText=False)
                txt = (
                    'The following file already exists:<br><br>'
                    f'<code>{filePath}</code><br><br>'
                    'Do you want to continue?'
                )
                _, noButton, yesButton = msg.warning(
                    self, 'File exists', txt, 
                    buttonsTexts=(
                        'Cancel',
                        'No, let me choose a different path',
                        'Yes, overwrite existing file'
                    )
                )
                if msg.cancel:
                    return ''
                if msg.clickedButton == yesButton:
                    break
        
        self.loadedFilename, ext = os.path.splitext(os.path.basename(filePath))
        self.parametersQGBox.saveToIniFile(filePath)
        self.lastSavedIniFilePath = filePath
        self.savingParamsFileDone(filePath)
        return filePath

    def savingParamsFileDone(self, filePath):
        txt = html_func.paragraph(
            'Parameters file successfully <b>saved</b> at the following path:'
        )
        msg = acdc_widgets.myMessageBox()
        msg.addShowInFileManagerButton(os.path.dirname(filePath))
        msg.information(self, 'Saving done!', txt, commands=(filePath,))
        
    def addInspectResultsTab(self):
        self.inspectResultsTab = InspectResultsTabWidget()
        self.addTab(self.inspectResultsTab, 'Inspect results')
    
    def addAutoTuneTab(self):
        self.autoTuneTabWidget = AutoTuneTabWidget()
        # self.autoTuneTabWidget.setDisabled(True)
        self.addTab(self.autoTuneTabWidget, 'Tune parameters')

class InspectResultsTabWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        mainLayout = QVBoxLayout()
        
        buttonsLayout = QHBoxLayout()
        
        self.loadAnalysisButton = acdc_widgets.OpenFilePushButton(
            'Load results from previous analysis...'
        )
        buttonsLayout.addWidget(self.loadAnalysisButton)
        buttonsLayout.addStretch(1)
        
        scrollArea = QScrollArea(self)
        scrollArea.setWidgetResizable(True)
        
        self.viewFeaturesGroupbox = AutoTuneViewSpotFeatures(
            parent=self, infoText=''
        )
        scrollArea.setWidget(self.viewFeaturesGroupbox)
        
        mainLayout.addLayout(buttonsLayout)
        mainLayout.addWidget(scrollArea)
        
        self.setLayout(mainLayout)
    
    def setInspectFeatures(self, point_features):
        if point_features is None:
            return
        self.viewFeaturesGroupbox.setFeatures(point_features)       
        

class AutoTuneGroupbox(QGroupBox):
    sigColorChanged = Signal(object, bool)
    sigFeatureSelected = Signal(object, str, str)
    sigYXresolMultiplChanged = Signal(float)
    sigZresolLimitChanged = Signal(float)
    sigYXresolMultiplActivated = Signal(bool)
    sigZresolLimitActivated = Signal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)

        mainLayout = QVBoxLayout()
        font = config.font()

        params = config.analysisInputsParams()
        self.params = {}
        for section, section_params in params.items():
            groupBox = None
            row = 0
            for anchor, param in section_params.items():
                tuneWidget = param.get('autoTuneWidget')
                if tuneWidget is None:
                    continue
                if section not in self.params:
                    self.params[section] = {}
                    self.params[section]['groupBox'] = QGroupBox(section)
                    self.params[section]['formLayout'] = widgets.FormLayout()
                self.params[section][anchor] = param.copy()
                groupBox = self.params[section]['groupBox']
                formLayout = self.params[section]['formLayout']
                formWidget = widgets.ParamFormWidget(
                    anchor, param, self, use_tune_widget=True
                )
                formLayout.addFormWidget(formWidget, row=row)
                self.params[section][anchor]['widget'] = formWidget.widget
                self.params[section][anchor]['formWidget'] = formWidget
                self.params[section][anchor]['groupBox'] = groupBox
                if anchor == 'gopThresholds':
                    formWidget.widget.sigFeatureSelected.connect(
                        self.emitFeatureSelected
                    )
                elif anchor == 'yxResolLimitMultiplier':
                    formWidget.widget.valueChanged.connect(
                        self.emitYXresolMultiplSigChanged
                    )
                    formWidget.widget.sigActivated.connect(
                        self.emitYXresolMultiplSigActivated
                    )
                    formWidget.widget.activateCheckbox.setChecked(True)
                    formWidget.widget.setDisabled(False)
                elif anchor == 'zResolutionLimit':
                    formWidget.widget.valueChanged.connect(
                        self.emitZresolLimitSigChanged
                    )
                    formWidget.widget.sigActivated.connect(
                        self.emitZresolLimitSigActivated
                    )
                    formWidget.widget.activateCheckbox.setChecked(False)
                    formWidget.widget.setDisabled(True)
                row += 1
            if groupBox is None:
                continue
            groupBox.setLayout(formLayout)
            mainLayout.addWidget(groupBox)
        
        autoTuneSpotProperties = AutoTuneSpotProperties() 
        self.trueFalseToggle = autoTuneSpotProperties.trueFalseToggle
        self.trueColorButton= autoTuneSpotProperties.trueColorButton
        self.falseColorButton = autoTuneSpotProperties.falseColorButton
        
        self.falseColorButton.sigColorChanging.connect(self.setFalseColor)
        
        self.trueItem = autoTuneSpotProperties.trueItem
        self.falseItem = autoTuneSpotProperties.falseItem
        self.autoTuneSpotProperties = autoTuneSpotProperties
        
        self.trueItem.sigClicked.connect(self.truePointsClicked)
        self.falseItem.sigClicked.connect(self.falsePointsClicked)
        
        self.trueItem.sigHovered.connect(self.truePointsHovered)
        self.falseItem.sigHovered.connect(self.falsePointsHovered)
        
        self.viewFeaturesGroupbox = AutoTuneViewSpotFeatures()
        
        mainLayout.addWidget(autoTuneSpotProperties)
        mainLayout.addWidget(self.viewFeaturesGroupbox)
        mainLayout.addStretch(1)
        self.setLayout(mainLayout)
        self.setFont(font)
    
    def setInspectFeatures(self, point_features):
        self.viewFeaturesGroupbox.setFeatures(point_features)
    
    def emitYXresolMultiplSigChanged(self, value):
        self.sigYXresolMultiplChanged.emit(value)
    
    def emitZresolLimitSigChanged(self, value):
        self.sigZresolLimitChanged.emit(value)
    
    def emitYXresolMultiplSigActivated(self, checked):
        self.sigYXresolMultiplActivated.emit(checked)

    def emitZresolLimitSigActivated(self, checked):
        self.sigZresolLimitActivated.emit(checked)
    
    def emitFeatureSelected(self, button, featureText, colName):
        self.sigFeatureSelected.emit(button, featureText, colName)
    
    def falsePointsClicked(self, item, points, event):
        pass
    
    def falsePointsHovered(self, item, points, event):
        pass
    
    def truePointsClicked(self, item, points, event):
        pass
    
    def truePointsHovered(self, item, points, event):
        pass
    
    def setFalseColor(self, colorButton):
        r, g, b, a = colorButton.color().getRgb()
        self.falseItem.setBrush(r, g, b, 50)
        self.falseItem.setPen(r, g, b, width=2)
        self.sigColorChanged.emit((r, g, b, a), False)
    
    def setTrueColor(self, colorButton):
        r, g, b, a = colorButton.color().getRgb()
        self.trueItem.setBrush(r, g, b, 50)
        self.trueItem.setPen(r, g, b, width=2)
        self.sigColorChanged.emit((r, g, b, a), True)

class AutoTuneSpotProperties(QGroupBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setTitle('Spots properties')
        layout = QVBoxLayout()
        
        trueFalseToggleLayout = QHBoxLayout()
                
        trueFalseToggleLayout.addWidget(
            QLabel('True spots color'), alignment=Qt.AlignRight
        )
        self.trueColorButton = acdc_widgets.myColorButton(
            color=(255, 0, 0)
        )
        trueFalseToggleLayout.addWidget(
            self.trueColorButton, alignment=Qt.AlignCenter
        )
        trueFalseToggleLayout.addStretch(1)        
        
        trueFalseToggleLayout.addWidget(
            QLabel('False spots color'), alignment=Qt.AlignRight
        )
        self.falseColorButton = acdc_widgets.myColorButton(
            color=(0, 255, 255)
        )
        trueFalseToggleLayout.addWidget(
            self.falseColorButton, alignment=Qt.AlignCenter
        )
        trueFalseToggleLayout.addStretch(1) 
        
        trueFalseToggleLayout.addWidget(
            QLabel('Clicking on true spots'), alignment=Qt.AlignRight
        )
        self.trueFalseToggle = acdc_widgets.Toggle()
        self.trueFalseToggle.setChecked(True)
        trueFalseToggleLayout.addWidget(
            self.trueFalseToggle, alignment=Qt.AlignCenter
        )
        layout.addLayout(trueFalseToggleLayout)
        
        clearPointsButtonsLayout = QHBoxLayout()
        
        clearFalsePointsButton = acdc_widgets.eraserPushButton('Clear false points')
        clearTruePointsButton = acdc_widgets.eraserPushButton('Clear true points')
        clearAllPointsButton = acdc_widgets.eraserPushButton('Clear all points')
        clearPointsButtonsLayout.addWidget(clearFalsePointsButton)
        clearPointsButtonsLayout.addWidget(clearTruePointsButton)
        clearPointsButtonsLayout.addWidget(clearAllPointsButton)
        layout.addSpacing(10)
        layout.addLayout(clearPointsButtonsLayout)
        
        clearFalsePointsButton.clicked.connect(self.clearFalsePoints)
        clearTruePointsButton.clicked.connect(self.clearTruePoints)
        clearAllPointsButton.clicked.connect(self.clearAllPoints)
        
        self.trueColorButton.sigColorChanging.connect(self.setTrueColor)
        self.falseColorButton.sigColorChanging.connect(self.setFalseColor)
        
        self.trueItem = acdc_widgets.ScatterPlotItem(
            symbol='o', size=3, pxMode=False,
            brush=pg.mkBrush((255,0,0,50)),
            pen=pg.mkPen((255,0,0), width=2),
            hoverable=True, hoverPen=pg.mkPen((255,0,0), width=3),
            hoverBrush=pg.mkBrush((255,0,0)), tip=None
        )
        
        self.falseItem = acdc_widgets.ScatterPlotItem(
            symbol='o', size=3, pxMode=False,
            brush=pg.mkBrush((0,255,255,50)),
            pen=pg.mkPen((0,255,255), width=2),
            hoverable=True, hoverPen=pg.mkPen((0,255,255), width=3),
            hoverBrush=pg.mkBrush((0,255,255)), tip=None
        )
        self.setLayout(layout)
    
    def setFalseColor(self, colorButton):
        r, g, b, a = colorButton.color().getRgb()
        self.falseItem.setBrush(r, g, b, 50)
        self.falseItem.setPen(r, g, b, width=2)
        self.sigColorChanged.emit((r, g, b, a), False)
    
    def setTrueColor(self, colorButton):
        r, g, b, a = colorButton.color().getRgb()
        self.trueItem.setBrush(r, g, b, 50)
        self.trueItem.setPen(r, g, b, width=2)
        self.sigColorChanged.emit((r, g, b, a), True)
    
    def clearFalsePoints(self):
        self.trueItem.setVisible(False)
        self.trueItem.clear()
    
    def clearTruePoints(self):
        self.falseItem.setVisible(False)
        self.falseItem.clear()

    def clearAllPoints(self):
        self.trueItem.setVisible(False)
        self.falseItem.setVisible(False)
        self.trueItem.clear()
        self.falseItem.clear()

class AutoTuneViewSpotFeatures(QGroupBox):
    def __init__(self, parent=None, infoText=None):
        super().__init__(parent)
        
        self.setTitle('Features of the spot under mouse cursor')
        
        mainLayout = QVBoxLayout()
        
        layout = QGridLayout()
        
        col = 0
        row = 0
        if infoText is None:
            txt = html_func.span(
                '<i>Add some points and run autotuning to view spots features</i>',
                font_color='red'
            )
        else:
            txt = infoText
        self._infoText = txt
        self.infoLabel = QLabel(txt)
        layout.addWidget(self.infoLabel, row, col, 1, 2, alignment=Qt.AlignCenter)
        
        row += 1
        layout.addWidget(QLabel('x coordinate'), row, col, alignment=Qt.AlignRight)
        self.xLineEntry = widgets.ReadOnlyLineEdit()
        layout.addWidget(self.xLineEntry, row, col+1)
        
        row += 1
        layout.addWidget(QLabel('y coordinate'), row, col, alignment=Qt.AlignRight)
        self.yLineEntry = widgets.ReadOnlyLineEdit()
        layout.addWidget(self.yLineEntry, row, col+1)
        
        row += 1
        layout.addWidget(QLabel('z coordinate'), row, col, alignment=Qt.AlignRight)
        self.zLineEntry = widgets.ReadOnlyLineEdit()
        layout.addWidget(self.zLineEntry, row, col+1)
        
        row += 1
        self.selectButton = widgets.FeatureSelectorButton(
            'Click to select feature to view...  ', alignment='right'
        )
        self.selectButton.setSizeLongestText(
            'Spotfit intens. metric, Foregr. integral gauss. peak'
        )
        self.selectButton.clicked.connect(self.selectFeature)
        self.selectButton.entry = widgets.ReadOnlyLineEdit()
        self.addFeatureButton = acdc_widgets.addPushButton()
        layout.addWidget(self.selectButton, row, col)
        layout.addWidget(self.selectButton.entry, row, col+1)
        layout.addWidget(
            self.addFeatureButton, row, col+2, alignment=Qt.AlignLeft
        )
        self.featureButtons = [self.selectButton]
        self.addFeatureButton.clicked.connect(self.addFeatureEntry)
        
        self.nextRow = row + 1
        
        self._layout = layout
        
        mainLayout.addLayout(layout)
        mainLayout.addStretch(1)
        self.setLayout(mainLayout)
    
    def resetFeatures(self):
        self.infoLabel.setText(self._infoText)
    
    def setFeatures(self, point_features: pd.Series):
        frame_i, z, y, x = point_features.name
        self.xLineEntry.setText(str(x))
        self.yLineEntry.setText(str(y))
        self.zLineEntry.setText(str(z))
        for selectButton in self.featureButtons:
            feature_colname = selectButton.toolTip()
            if feature_colname not in point_features.index:
                continue
            value = point_features.loc[feature_colname]
            selectButton.entry.setText(str(value))
        self.infoLabel.setText('<i>&nbsp;</i>')
    
    def addFeatureEntry(self):
        selectButton = widgets.FeatureSelectorButton(
            'Click to select feature to view...  ', alignment='right'
        )
        selectButton.setSizeLongestText(
            'Spotfit intens. metric, Foregr. integral gauss. peak'
        )
        selectButton.clicked.connect(self.selectFeature)
        selectButton.entry = widgets.ReadOnlyLineEdit()
        delButton = acdc_widgets.delPushButton()
        delButton.widgets = [selectButton, selectButton.entry]
        delButton.selector = selectButton
        delButton.clicked.connect(self.removeFeatureField)
        
        self._layout.addWidget(selectButton, self.nextRow, 0)
        self._layout.addWidget(selectButton.entry, self.nextRow, 1)
        self._layout.addWidget(
            delButton, self.nextRow, 2, alignment=Qt.AlignLeft
        )
        self.nextRow += 1
        
        self.featureButtons.append(selectButton)
        
    def removeFeatureField(self):
        delButton = self.sender()
        for widget in delButton.widgets:
            self._layout.removeWidget(widget)
        self._layout.removeWidget(delButton)
        self.featureButtons.remove(delButton.selector)
    
    def getFeatureGroup(self):
        if self.selectButton.text().find('Click') != -1:
            return ''

        text = self.selectButton.text()
        topLevelText, childText = text.split(', ')
        return {topLevelText: childText}
    
    def selectFeature(self):
        self.selectFeatureDialog = widgets.FeatureSelectorDialog(
            parent=self.sender(), multiSelection=False, 
            expandOnDoubleClick=True, isTopLevelSelectable=False, 
            infoTxt='Select feature', allItemsExpanded=False
        )
        self.selectFeatureDialog.setCurrentItem(self.getFeatureGroup())
        # self.selectFeatureDialog.resizeVertical()
        self.selectFeatureDialog.sigClose.connect(self.setFeatureText)
        self.selectFeatureDialog.show()
    
    def setFeatureText(self):
        if self.selectFeatureDialog.cancel:
            return
        selectButton = self.selectFeatureDialog.parent()
        selectButton.setFlat(True)
        selection = self.selectFeatureDialog.selectedItems()
        group_name = list(selection.keys())[0]
        feature_name = selection[group_name][0]
        featureText = f'{group_name}, {feature_name}'
        selectButton.setFeatureText(featureText)
        column_name = features.feature_names_to_col_names_mapper()[featureText]
        selectButton.setToolTip(f'{column_name}')

class AutoTuneTabWidget(QWidget):
    sigStartAutoTune = Signal(object)
    sigStopAutoTune = Signal(object)
    sigTrueFalseToggled = Signal(object)
    sigColorChanged = Signal(object, bool)
    sigFeatureSelected = Signal(object, str, str)
    sigAddAutoTunePointsToggle = Signal(bool)
    sigYXresolMultiplChanged = Signal(float)
    sigZresolLimitChanged = Signal(float)
    sigYXresolMultiplActivated = Signal(bool)
    sigZresolLimitActivated = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout()
        
        self.df_features = None
        
        self.isYXresolMultiplActive = True
        self.isZresolLimitActive = False

        buttonsLayout = QHBoxLayout()
        helpButton = acdc_widgets.helpPushButton('Help...')
        buttonsLayout.addWidget(helpButton)
        buttonsLayout.addStretch(1)
        
        autoTuningButton = widgets.AutoTuningButton()
        self.loadingCircle = acdc_widgets.LoadingCircleAnimation(size=16)
        self.loadingCircle.setVisible(False)
        buttonsLayout.addWidget(self.loadingCircle)
        buttonsLayout.addWidget(autoTuningButton)
        self.autoTuningButton = autoTuningButton
        
        # Start adding points autotune button
        self.addAutoTunePointsButton = widgets.AddAutoTunePointsButton()
        buttonsLayout.addWidget(self.addAutoTunePointsButton)

        autoTuneScrollArea = QScrollArea(self)
        autoTuneScrollArea.setWidgetResizable(True)

        self.autoTuneGroupbox = AutoTuneGroupbox(parent=self)
        autoTuneScrollArea.setWidget(self.autoTuneGroupbox)

        layout.addLayout(buttonsLayout)
        layout.addWidget(autoTuneScrollArea)
        # layout.addStretch(1)
        # layout.addWidget(self.autoTuneGroupbox)
        self.setLayout(layout)

        autoTuningButton.sigToggled.connect(self.emitAutoTuningSignal)
        self.addAutoTunePointsButton.sigToggled.connect(
            self.emitAddAutoTunePointsToggle
        )
        self.autoTuneGroupbox.trueFalseToggle.toggled.connect(
            self.emitForegrBackrToggledSignal
        )
        self.autoTuneGroupbox.sigColorChanged.connect(
            self.emitColorChanged
        )
        self.autoTuneGroupbox.sigFeatureSelected.connect(
            self.emitFeatureSelected
        )
        self.autoTuneGroupbox.sigYXresolMultiplChanged.connect(
            self.emitYXresolMultiplSigChanged
        )
        self.autoTuneGroupbox.sigZresolLimitChanged.connect(
            self.emitZresolLimitSigChanged
        )
        self.autoTuneGroupbox.sigYXresolMultiplActivated.connect(
            self.emitYXresolMultiplSigActivated
        )
        self.autoTuneGroupbox.sigZresolLimitActivated.connect(
            self.emitZresolLimitSigActivated
        )
        helpButton.clicked.connect(self.showHelp)
    
    def emitYXresolMultiplSigChanged(self, value):
        self.sigYXresolMultiplChanged.emit(value)
    
    def emitZresolLimitSigChanged(self, value):
        self.sigZresolLimitChanged.emit(value)
    
    def emitYXresolMultiplSigActivated(self, checked):
        self.sigYXresolMultiplActivated.emit(checked)
        self.isYXresolMultiplActive = True
        self.isZresolLimitActive = False

    def emitZresolLimitSigActivated(self, checked):
        self.sigZresolLimitActivated.emit(checked)
        self.isYXresolMultiplActive = False
        self.isZresolLimitActive = True
    
    def emitFeatureSelected(self, button, featureText, colName):
        self.sigFeatureSelected.emit(button, featureText, colName)
    
    def emitAddAutoTunePointsToggle(self, button, checked):
        self.setAutoTuneItemsVisible(True)
        self.sigAddAutoTunePointsToggle.emit(checked)
        self.addAutoTunePointsButton.clearFocus()
    
    def emitColorChanged(self, color: tuple, true_spots: bool):
        self.sigColorChanged.emit(color, true_spots)
    
    def emitAutoTuningSignal(self, button, started):
        self.loadingCircle.setVisible(started)
        if started:
            self.sigStartAutoTune.emit(self)
        else:
            self.sigStopAutoTune.emit(self)
    
    def setAutoTuneItemsVisible(self, visible):
        self.autoTuneGroupbox.trueItem.setVisible(visible)
        self.autoTuneGroupbox.falseItem.setVisible(visible)
    
    def setInspectFeatures(self, points):
        if self.df_features is None:
            return
        point = points[0]
        frame_i, z = point.data()
        pos = point.pos()
        x, y = round(pos.x()), round(pos.y())
        point_features = self.df_features.loc[(frame_i, z, y, x)]
        self.autoTuneGroupbox.setInspectFeatures(point_features)
    
    def emitForegrBackrToggledSignal(self, checked):
        self.sigTrueFalseToggled.emit(checked)
    
    def initAutoTuneColors(self, trueColor, falseColor):
        self.autoTuneGroupbox.trueColorButton.setColor(trueColor)
        self.autoTuneGroupbox.falseColorButton.setColor(falseColor)
    
    def selectedFeatures(self):
        SECTION = 'Spots channel'
        ANCHOR = 'gopThresholds'
        widget = self.autoTuneGroupbox.params[SECTION][ANCHOR]['widget']
        selectedFeatures = {
            groupbox.title(): [None, None] 
            for groupbox in widget.featureGroupboxes.values()
            if groupbox.title().find('Click') == -1
        }    
        return selectedFeatures
    
    def setTuneResult(self, tuneResult: tune.TuneResult):
        SECTION = 'Spots channel'
        ANCHOR = 'gopThresholds'
        widget = self.autoTuneGroupbox.params[SECTION][ANCHOR]['widget']
        for groupbox in widget.featureGroupboxes.values():
            feature_name = groupbox.title()
            if feature_name not in tuneResult.features_range:
                continue
            minimum, maximum = tuneResult.features_range[feature_name]
            groupbox.setRange(minimum, maximum)
        
        ANCHOR = 'spotThresholdFunc'
        widget = self.autoTuneGroupbox.params[SECTION][ANCHOR]['widget']
        widget.setText(tuneResult.threshold_method)
        
        self.autoTuneGroupbox.viewFeaturesGroupbox.infoLabel.setText(
            '<i>Hover mouse cursor on points to view features</i>'
        )
        self.df_features = (
            tuneResult.df_features.reset_index()
            .set_index(['frame_i', 'z', 'y', 'x'])
        )
    
    def getHoveredPoints(self, frame_i, z, y, x):
        items = [
            self.autoTuneGroupbox.trueItem, self.autoTuneGroupbox.falseItem
        ]
        hoveredPoints = []
        for item in items:
            hoveredMask = item._maskAt(QPointF(x, y))
            points = item.points()[hoveredMask][::-1]
            if len(points) == 0:
                continue
            for point in points:
                if point.data() != (frame_i, z):
                    continue 
                hoveredPoints.append(point)
        return hoveredPoints
    
    def addAutoTunePoint(self, frame_i, z, x, y):
        if self.autoTuneGroupbox.trueFalseToggle.isChecked():
            item = self.autoTuneGroupbox.trueItem
            item.setVisible(True)
        else:
            item = self.autoTuneGroupbox.falseItem
            item.setVisible(True)
        hoveredMask = item._maskAt(QPointF(x, y))
        points = item.points()[hoveredMask][::-1]
        if len(points) > 0:
            for point in points:
                if point.data() != (frame_i, z):
                    continue 
                item.removePoint(point._index)
        else:
            item.addPoints([x], [y], data=[(frame_i, z)])
        
        self.resetFeatures()
    
    def resetFeatures(self):
        self.df_features = None
        self.autoTuneGroupbox.viewFeaturesGroupbox.resetFeatures()
    
    def setVisibleAutoTunePoints(self, frame_i, z):
        items = [
            self.autoTuneGroupbox.trueItem, self.autoTuneGroupbox.falseItem
        ]
        
        for item in items:
            brushes = []
            pens = []
            for point in item.data['item']:
                visible = point.data() == (frame_i, z)
                if not visible:
                    brush = pg.mkBrush((0, 0, 0, 0))
                    pen = pg.mkPen((0, 0, 0, 0))
                else:
                    brush = item.itemBrush()
                    pen = item.itemPen()
                brushes.append(brush)
                pens.append(pen)
            if not brushes:
                continue
            item.setBrush(brushes)
            item.setPen(pens)
            
    def setAutoTunePointSize(self, size):
        self.autoTuneGroupbox.trueItem.setSize(size)
        self.autoTuneGroupbox.falseItem.setSize(size)
    
    def showHelp(self):
        msg = acdc_widgets.myMessageBox()
        steps = [
    'Load images (<code>Open folder</code> button on the top toolbar).',
    'Select the features used to filter true spots.',
    'Click <code>Start autotuning</code> on the "Autotune parameters" tab.',
    'Choose whether to use the current spots segmentation mask.',
    'Adjust spot size with up/down arrow keys.',
    'Click on the true spots on the image.'
        ]
        txt = html_func.paragraph(f"""
            Autotuning can be used to interactively determine the 
            <b>optimal parameters</b> for the analysis.<br><br>
            Instructions:{acdc_html.to_list(steps, ordered=True)}<br>
            Select as many features as you want. The tuning process will then 
            optimise their values that will be used to filter true spots.<br><br>
            The more true spots you add, the better the optimisation process 
            will be. However, adding the spots that are 
            <b>more difficult to detect</b> (e.g., out-of-focus or dim) 
            should yield <b>better results</b>.
        """)
        msg.information(self, 'Autotuning instructions', txt)
    
    def setDisabled(self, disabled: bool) -> None:
        self.autoTuneGroupbox.setDisabled(disabled)
        self.autoTuningButton.setDisabled(disabled)

class ParamsGroupBox(QGroupBox):
    sigResolMultiplValueChanged = Signal(float)
    
    def __init__(self, parent=None, debug=False, logging_func=print):
        super().__init__(parent)

        self.selectedMeasurements = None
        # mainLayout = QGridLayout(self)
        mainLayout = QVBoxLayout()

        self.logging_func = logging_func
        
        section_option_to_desc_mapper = docs.get_params_desc_mapper()
        
        font = config.font()

        _params = config.analysisInputsParams()
        self.params = {}
        for section, section_params in _params.items():
            formLayout = widgets.FormLayout()
            self.params[section] = {}
            isNotCheckableGroup = (
                section == 'File paths and channels' or section == 'METADATA'
                or section == 'Pre-processing'
            )
            
            if section == 'SpotFIT':
                groupBox = widgets.ExpandableGroupbox(section)
                groupBox.setExpanded(False)
            else:
                groupBox = QGroupBox(section)
            
            if isNotCheckableGroup:
                groupBox.setCheckable(False)
            else:
                groupBox.setCheckable(True)
            groupBox.setFont(font)
            groupBox.formWidgets = []
            for row, (anchor, param) in enumerate(section_params.items()):
                self.params[section][anchor] = param.copy()
                formWidget = widgets.ParamFormWidget(
                    anchor, param, self, 
                    section_option_to_desc_mapper=section_option_to_desc_mapper
                )
                formWidget.section = section
                formWidget.sigLinkClicked.connect(self.infoLinkClicked)
                self.connectFormWidgetButtons(formWidget, param)
                formLayout.addFormWidget(formWidget, row=row)
                self.params[section][anchor]['widget'] = formWidget.widget
                self.params[section][anchor]['formWidget'] = formWidget
                self.params[section][anchor]['groupBox'] = groupBox
                if formWidget.useEditableLabel:
                    self.params[section][anchor]['desc'] = (
                        formWidget.labelLeft.text()
                    )
                    
                if formWidget.addFieldButton is not None:
                    formWidget.sigAddField.connect(
                        self.addFieldToParams
                    )
                    formWidget.sigRemoveField.connect(
                        self.removeFieldFromParams
                    )
                
                groupBox.formWidgets.append(formWidget)

                isGroupChecked = param.get('isSectionInConfig', False)
                groupBox.setChecked(isGroupChecked)

                if param.get('editSlot') is not None:
                    editSlot = param.get('editSlot')
                    slot = getattr(self, editSlot)
                    formWidget.sigEditClicked.connect(slot)
                actions = param.get('actions', None)
                if actions is None:
                    continue

                for action in actions:
                    signal = getattr(formWidget.widget, action[0])
                    signal.connect(getattr(self, action[1]))

            groupBox.setLayout(formLayout)
            mainLayout.addWidget(groupBox)

        # mainLayout.addStretch()

        self.setLayout(mainLayout)
        self.updateMinSpotSize()
    
    def addFieldToParams(self, formWidget):
        if formWidget.fieldIdx == 0:
            return
        
        section = formWidget.section
        anchor = formWidget.anchor
        groupBox = self.params[section][anchor]['groupBox']

        defaultParams = config.getDefaultParams()
        added_anchor = f'{anchor}_{formWidget.fieldIdx}'
        self.params[section][added_anchor] = defaultParams[section][anchor]
        anchor = added_anchor
        
        self.params[section][anchor]['widget'] = formWidget.widget
        self.params[section][anchor]['formWidget'] = formWidget
        self.params[section][anchor]['groupBox'] = groupBox        
        if formWidget.useEditableLabel:
            self.params[section][anchor]['desc'] = (
                formWidget.labelLeft.text()
            )   
    
    def removeFieldFromParams(self, section, anchor, fieldIdx):
        if fieldIdx > 0:
            anchor = f'{anchor}_{fieldIdx}'
        self.params[section].pop(anchor)
    
    def addFoldersToAnalyse(self, formWidget):
        preSelectedPaths = formWidget.widget.text().split('\n')
        preSelectedPaths = [path for path in preSelectedPaths if path]
        if not preSelectedPaths:
            preSelectedPaths = None
        win = SelectFolderToAnalyse(preSelectedPaths=preSelectedPaths)
        win.exec_()
        if win.cancel:
            return
        selectedPathsList = win.paths
        selectedPaths = '\n'.join(selectedPathsList)
        formWidget.widget.setText(selectedPaths)
    
    def _getCallbackFunction(self, callbackFuncPath):
        moduleName, functionName = callbackFuncPath.split('.')
        module = globals()[moduleName]
        return getattr(module, functionName)
    
    def connectFormWidgetButtons(self, formWidget, paramValues):
        editButtonCallback = paramValues.get('editButtonCallback')        
        if editButtonCallback is not None:
            function = self._getCallbackFunction(editButtonCallback)
            formWidget.sigEditClicked.connect(function)

    def infoLinkClicked(self, link):
        try:
            # Stop previously blinking controls, if any
            self.blinker.stopBlinker()
            self.labelBlinker.stopBlinker()
        except Exception as e:
            pass

        try:
            section, anchor, *option = link.split(';')
            formWidget = self.params[section][anchor]['formWidget']
            if option:
                option = option[0]
                widgetToBlink = getattr(formWidget, option)
            else:
                widgetToBlink = formWidget.widget
            self.blinker = utils.widgetBlinker(widgetToBlink)
            label = formWidget.labelLeft
            self.labelBlinker = utils.widgetBlinker(
                label, styleSheetOptions=('color',)
            )
            self.blinker.start()
            self.labelBlinker.start()
        except Exception as e:
            traceback.print_exc()

    def SizeZchanged(self, SizeZ):
        isZstack = SizeZ > 1
        metadata = self.params['METADATA']
        spotMinSizeLabels = metadata['spotMinSizeLabels']['widget']
        spotMinSizeLabels.setIsZstack(isZstack)
        self.updateMinSpotSize()
        
        preProcessParams = self.params['Pre-processing']
        extend3DsegmRangeFormWidget = (
            preProcessParams['extend3DsegmRange']['formWidget']
        )
        extend3DsegmRangeFormWidget.setDisabled(not isZstack)
    
    def zyxVoxelSize(self):
        metadata = self.params['METADATA']
        physicalSizeX = metadata['pixelWidth']['widget'].value()
        physicalSizeY = metadata['pixelHeight']['widget'].value()
        physicalSizeZ = metadata['voxelDepth']['widget'].value()
        return (physicalSizeZ, physicalSizeY, physicalSizeX)
    
    def updateMinSpotSize(self, value=0.0):
        metadata = self.params['METADATA']
        physicalSizeX = metadata['pixelWidth']['widget'].value()
        physicalSizeY = metadata['pixelHeight']['widget'].value()
        physicalSizeZ = metadata['voxelDepth']['widget'].value()
        SizeZ = metadata['SizeZ']['widget'].value()
        emWavelen = metadata['emWavelen']['widget'].value()
        NA = metadata['numAperture']['widget'].value()
        zResolutionLimit_um = metadata['zResolutionLimit']['widget'].value()
        yxResolMultiplier = metadata['yxResolLimitMultiplier']['widget'].value()
        zyxMinSize_pxl, zyxMinSize_um = core.calcMinSpotSize(
            emWavelen, NA, physicalSizeX, physicalSizeY, physicalSizeZ,
            zResolutionLimit_um, yxResolMultiplier
        )
        if SizeZ == 1:
            zyxMinSize_pxl = (float('nan'), *zyxMinSize_pxl[1:])
            zyxMinSize_um = (float('nan'), *zyxMinSize_um[1:])
        
        zyxMinSize_pxl_txt = (f'{[round(val, 4) for val in zyxMinSize_pxl]} pxl'
            .replace(']', ')')
            .replace('[', '(')
        )
        zyxMinSize_um_txt = (f'{[round(val, 4) for val in zyxMinSize_um]} μm'
            .replace(']', ')')
            .replace('[', '(')
        )
        spotMinSizeLabels = metadata['spotMinSizeLabels']['widget']
        spotMinSizeLabels.pixelLabel.setText(zyxMinSize_pxl_txt)
        spotMinSizeLabels.umLabel.setText(zyxMinSize_um_txt)
        
        self.sigResolMultiplValueChanged.emit(yxResolMultiplier)
        
        formWidget = metadata['spotMinSizeLabels']['formWidget']
        warningButton = formWidget.warningButton
        warningButton.hide()
        if any([val<2 for val in zyxMinSize_pxl]):
            warningButton.show()
            try:
                formWidget.sigWarningButtonClicked.disconnect()
            except Exception as err:
                pass
            formWidget.sigWarningButtonClicked.connect(
                self.warnSpotSizeMightBeTooLow
            )
    
    def warnSpotSizeMightBeTooLow(self, formWidget):
        spotMinSizeLabels = formWidget.widget.pixelLabel.text()
        txt = html_func.paragraph(f"""
            One or more radii of the <code>{formWidget.text()}</code> are 
            <b>less than 2 pixels</b>.<br><br>
            This means that spotMAX can detect spots that are 1 pixel away 
            along the dimension that is less than 2 pixels.<br><br>
            We recommend <b>increasing the radii to at least 3 pixels</b>.<br><br>
            Current <code>{formWidget.text()} = {spotMinSizeLabels}</code>
        """)
        msg = acdc_widgets.myMessageBox(wrapText=False)
        msg.warning(self, 'Minimimum spot size potentially too low', txt)
    
    def configIniParams(self):
        ini_params = {}
        for section, section_params in self.params.items():
            ini_params[section] = {}
            for anchor, options in section_params.items():
                groupbox = options['groupBox']
                initialVal = options['initialVal']
                widget = options['widget']
                if groupbox.isCheckable() and not groupbox.isChecked():
                    # Use default value if the entire section is not checked
                    value = initialVal
                elif isinstance(initialVal, bool):
                    value = widget.isChecked()
                elif isinstance(initialVal, str):
                    try:
                        value = widget.currentText()
                    except AttributeError:
                        value = widget.text()
                elif isinstance(initialVal, float) or isinstance(initialVal, int):
                    value = widget.value()
                else:
                    value = widget.value()
                
                try:
                    # Editable labels (see widgets.FormWidget) have dynamic 
                    # text for the description
                    formWidget = options['formWidget']
                    desc = formWidget.labelLeft.text()
                except Exception as err:
                    desc = options['desc']
                
                if not desc:
                    continue
                
                ini_params[section][anchor] = {
                    'desc': desc, 
                    'loadedVal': value, 
                    'initialVal': initialVal
                }
        
        ini_params = self.addNNetParams(ini_params, 'spots')
        ini_params = self.addNNetParams(ini_params, 'ref_ch')
        return ini_params
    
    def addNNetParams(self, ini_params, channel):
        if channel == 'spots':
            params = self.params['Spots channel']
            anchor = 'spotPredictionMethod'
        else:
            params = self.params['Reference channel']
            anchor = 'refChSegmentationMethod'
        
        widget = params[anchor]['widget']
        nnet_params = widget.nnet_params_to_ini_sections()
        bioimageio_model_params = (
            widget.bioimageio_model_params_to_ini_sections()
        )
        if nnet_params is None and bioimageio_model_params is None:
            return ini_params

        if bioimageio_model_params is None:
            section_id_name = 'neural_network'
        else:
            section_id_name = 'bioimageio_model'
        
        init_model_params, segment_model_params = nnet_params
        SECTION = f'{section_id_name}.init.{channel}'
        for key, value in init_model_params.items():
            if SECTION not in ini_params:
                ini_params[SECTION] = {}
            ini_params[SECTION][key] = {
                'desc': key, 'loadedVal': value, 'isParam': True
            }
        
        SECTION = f'{section_id_name}.segment.{channel}'
        for key, value in segment_model_params.items():
            if SECTION not in ini_params:
                ini_params[SECTION] = {}
            ini_params[SECTION][key] = {
                'desc': key, 'loadedVal': value, 'isParam': True
            }
        return ini_params
    
    def saveSelectedMeasurements(self, configPars, ini_filepath):
        if self.selectedMeasurements is None:
            return

        section = 'Single-spot measurements to save'
        configPars[section] = {}
        for key, value in self.selectedMeasurements['single_spot'].items():
            configPars[section][key] = value
        
        section = 'Aggregated measurements to save'
        configPars[section] = {}
        for key, value in self.selectedMeasurements['aggr'].items():
            configPars[section][key] = value
        
        with open(ini_filepath, 'w', encoding="utf-8") as file:
            configPars.write(file)
    
    def setSelectedMeasurements(self, ini_filepath):
        cp = config.ConfigParser()
        cp.read(ini_filepath)
        tabKeys_sections = [
            ('single_spot', 'Single-spot measurements to save'),
            ('aggr', 'Aggregated measurements to save')
        ]
        self.selectedMeasurements = {}
        for tabKey, section in tabKeys_sections:
            if not cp.has_section(section):
                continue
            
            self.selectedMeasurements[tabKey] = dict(cp[section])
            
        if not self.selectedMeasurements:
            self.selectedMeasurements = None
        
    
    def saveToIniFile(self, ini_filepath):
        params = self.configIniParams()
        configPars = io.writeConfigINI(params, ini_filepath)
        self.saveSelectedMeasurements(configPars, ini_filepath)
        print('-'*100)
        print(f'Configuration file saved to: "{ini_filepath}"')
        print('*'*100)

    def showInfo(self):
        print(self.sender().label.text())

class spotStyleDock(QDockWidget):
    sigOk = Signal(int)
    sigCancel = Signal()

    def __init__(self, title, parent=None):
        super().__init__(title, parent)

        frame = QFrame()

        mainLayout = QVBoxLayout()
        slidersLayout = QGridLayout()
        buttonsLayout = QHBoxLayout()

        row = 0
        self.transpSlider = widgets.sliderWithSpinBox(title='Opacity')
        self.transpSlider.setMaximum(100)
        slidersLayout.addWidget(self.transpSlider, row, 0)

        row += 1
        self.penWidthSlider = widgets.sliderWithSpinBox(title='Contour thickness')
        self.penWidthSlider.setMaximum(20)
        self.penWidthSlider.setMinimum(1)
        slidersLayout.addWidget(self.penWidthSlider, row, 0)

        row += 1
        self.sizeSlider = widgets.sliderWithSpinBox(title='Size')
        self.sizeSlider.setMaximum(100)
        self.sizeSlider.setMinimum(1)
        slidersLayout.addWidget(self.sizeSlider, row, 0)

        okButton = acdc_widgets.okPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)

        cancelButton = acdc_widgets.cancelPushButton('Cancel')

        buttonsLayout.addStretch(1)
        buttonsLayout.addWidget(cancelButton)
        buttonsLayout.addSpacing(20)
        buttonsLayout.addWidget(okButton)
        
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(slidersLayout)
        mainLayout.addLayout(buttonsLayout)

        frame.setLayout(mainLayout)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setWidget(frame)
        self.setFloating(True)

        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetFloatable | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )

    def ok_cb(self):
        self.hide()

    def cancel_cb(self):
        self.sigCancel.emit()
        self.hide()

    def show(self):
        QDockWidget.show(self)
        self.resize(int(self.width()*1.5), self.height())
        self.setFocus()
        self.activateWindow()


class QDialogMetadata(QBaseDialog):
    def __init__(
            self, SizeT, SizeZ, TimeIncrement,
            PhysicalSizeZ, PhysicalSizeY, PhysicalSizeX,
            ask_SizeT, ask_TimeIncrement, ask_PhysicalSizes, numPos,
            parent=None, font=None, imgDataShape=None, PosData=None,
            fileExt='.tif'
        ):
        self.cancel = True
        self.ask_TimeIncrement = ask_TimeIncrement
        self.ask_PhysicalSizes = ask_PhysicalSizes
        self.imgDataShape = imgDataShape
        self.PosData = PosData
        self.fileExt = fileExt
        super().__init__(parent)
        self.setWindowTitle('Image properties')

        mainLayout = QVBoxLayout()
        loadingSizesGroupbox = QGroupBox()
        loadingSizesGroupbox.setTitle('Parameters for loading')
        metadataGroupbox = QGroupBox()
        metadataGroupbox.setTitle('Image Properties')
        buttonsLayout = QGridLayout()

        loadingParamLayout = QGridLayout()
        row = 0
        loadingParamLayout.addWidget(
            QLabel('Number of Positions to load'), row, 0,
            alignment=Qt.AlignRight
        )
        self.loadSizeS_SpinBox = widgets.QSpinBoxOdd(acceptedValues=(numPos,))
        self.loadSizeS_SpinBox.setMinimum(1)
        self.loadSizeS_SpinBox.setMaximum(numPos)
        self.loadSizeS_SpinBox.setValue(numPos)
        if numPos == 1:
            self.loadSizeS_SpinBox.setDisabled(True)
        self.loadSizeS_SpinBox.setAlignment(Qt.AlignCenter)
        loadingParamLayout.addWidget(self.loadSizeS_SpinBox, row, 1)

        row += 1
        loadingParamLayout.addWidget(
            QLabel('Number of frames to load'), row, 0, alignment=Qt.AlignRight
        )
        self.loadSizeT_SpinBox = widgets.QSpinBoxOdd(acceptedValues=(SizeT,))
        self.loadSizeT_SpinBox.setMinimum(1)
        if ask_SizeT:
            self.loadSizeT_SpinBox.setMaximum(SizeT)
            self.loadSizeT_SpinBox.setValue(SizeT)
            if fileExt != '.h5':
                self.loadSizeT_SpinBox.setDisabled(True)
        else:
            self.loadSizeT_SpinBox.setMaximum(1)
            self.loadSizeT_SpinBox.setValue(1)
            self.loadSizeT_SpinBox.setDisabled(True)
        self.loadSizeT_SpinBox.setAlignment(Qt.AlignCenter)
        loadingParamLayout.addWidget(self.loadSizeT_SpinBox, row, 1)

        row += 1
        loadingParamLayout.addWidget(
            QLabel('Number of z-slices to load'), row, 0,
            alignment=Qt.AlignRight
        )
        self.loadSizeZ_SpinBox = widgets.QSpinBoxOdd(acceptedValues=(SizeZ,))
        self.loadSizeZ_SpinBox.setMinimum(1)
        if SizeZ > 1:
            self.loadSizeZ_SpinBox.setMaximum(SizeZ)
            self.loadSizeZ_SpinBox.setValue(SizeZ)
            if fileExt != '.h5':
                self.loadSizeZ_SpinBox.setDisabled(True)
        else:
            self.loadSizeZ_SpinBox.setMaximum(1)
            self.loadSizeZ_SpinBox.setValue(1)
            self.loadSizeZ_SpinBox.setDisabled(True)
        self.loadSizeZ_SpinBox.setAlignment(Qt.AlignCenter)
        loadingParamLayout.addWidget(self.loadSizeZ_SpinBox, row, 1)

        loadingParamLayout.setColumnMinimumWidth(1, 100)
        loadingSizesGroupbox.setLayout(loadingParamLayout)

        gridLayout = QGridLayout()
        row = 0
        gridLayout.addWidget(
            QLabel('Number of frames (SizeT)'), row, 0, alignment=Qt.AlignRight
        )
        self.SizeT_SpinBox = QSpinBox()
        self.SizeT_SpinBox.setMinimum(1)
        self.SizeT_SpinBox.setMaximum(2147483647)
        if ask_SizeT:
            self.SizeT_SpinBox.setValue(SizeT)
        else:
            self.SizeT_SpinBox.setValue(1)
            self.SizeT_SpinBox.setDisabled(True)
        self.SizeT_SpinBox.setAlignment(Qt.AlignCenter)
        self.SizeT_SpinBox.valueChanged.connect(self.TimeIncrementShowHide)
        gridLayout.addWidget(self.SizeT_SpinBox, row, 1)

        row += 1
        gridLayout.addWidget(
            QLabel('Number of z-slices (SizeZ)'), row, 0, alignment=Qt.AlignRight
        )
        self.SizeZ_SpinBox = QSpinBox()
        self.SizeZ_SpinBox.setMinimum(1)
        self.SizeZ_SpinBox.setMaximum(2147483647)
        self.SizeZ_SpinBox.setValue(SizeZ)
        self.SizeZ_SpinBox.setAlignment(Qt.AlignCenter)
        self.SizeZ_SpinBox.valueChanged.connect(self.SizeZvalueChanged)
        gridLayout.addWidget(self.SizeZ_SpinBox, row, 1)

        row += 1
        self.TimeIncrementLabel = QLabel('Time interval (s)')
        gridLayout.addWidget(
            self.TimeIncrementLabel, row, 0, alignment=Qt.AlignRight
        )
        self.TimeIncrementSpinBox = QDoubleSpinBox()
        self.TimeIncrementSpinBox.setDecimals(7)
        self.TimeIncrementSpinBox.setMaximum(2147483647.0)
        self.TimeIncrementSpinBox.setValue(TimeIncrement)
        self.TimeIncrementSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.TimeIncrementSpinBox, row, 1)

        if SizeT == 1 or not ask_TimeIncrement:
            self.TimeIncrementSpinBox.hide()
            self.TimeIncrementLabel.hide()

        row += 1
        self.PhysicalSizeZLabel = QLabel('Physical Size Z (um/pixel)')
        gridLayout.addWidget(
            self.PhysicalSizeZLabel, row, 0, alignment=Qt.AlignRight
        )
        self.PhysicalSizeZSpinBox = QDoubleSpinBox()
        self.PhysicalSizeZSpinBox.setDecimals(7)
        self.PhysicalSizeZSpinBox.setMaximum(2147483647.0)
        self.PhysicalSizeZSpinBox.setValue(PhysicalSizeZ)
        self.PhysicalSizeZSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.PhysicalSizeZSpinBox, row, 1)

        if SizeZ==1 or not ask_PhysicalSizes:
            self.PhysicalSizeZSpinBox.hide()
            self.PhysicalSizeZLabel.hide()

        row += 1
        self.PhysicalSizeYLabel = QLabel('Physical Size Y (um/pixel)')
        gridLayout.addWidget(
            self.PhysicalSizeYLabel, row, 0, alignment=Qt.AlignRight
        )
        self.PhysicalSizeYSpinBox = QDoubleSpinBox()
        self.PhysicalSizeYSpinBox.setDecimals(7)
        self.PhysicalSizeYSpinBox.setMaximum(2147483647.0)
        self.PhysicalSizeYSpinBox.setValue(PhysicalSizeY)
        self.PhysicalSizeYSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.PhysicalSizeYSpinBox, row, 1)

        if not ask_PhysicalSizes:
            self.PhysicalSizeYSpinBox.hide()
            self.PhysicalSizeYLabel.hide()

        row += 1
        self.PhysicalSizeXLabel = QLabel('Physical Size X (um/pixel)')
        gridLayout.addWidget(
            self.PhysicalSizeXLabel, row, 0, alignment=Qt.AlignRight
        )
        self.PhysicalSizeXSpinBox = QDoubleSpinBox()
        self.PhysicalSizeXSpinBox.setDecimals(7)
        self.PhysicalSizeXSpinBox.setMaximum(2147483647.0)
        self.PhysicalSizeXSpinBox.setValue(PhysicalSizeX)
        self.PhysicalSizeXSpinBox.setAlignment(Qt.AlignCenter)
        gridLayout.addWidget(self.PhysicalSizeXSpinBox, row, 1)

        if not ask_PhysicalSizes:
            self.PhysicalSizeXSpinBox.hide()
            self.PhysicalSizeXLabel.hide()

        self.SizeZvalueChanged(SizeZ)

        gridLayout.setColumnMinimumWidth(1, 100)
        metadataGroupbox.setLayout(gridLayout)

        if numPos == 1:
            okTxt = 'Apply only to this Position'
        else:
            okTxt = 'Ok for loaded Positions'
        okButton = acdc_widgets.okPushButton(okTxt)
        okButton.setToolTip(
            'Save metadata only for current positionh'
        )
        okButton.setShortcut(Qt.Key_Enter)
        self.okButton = okButton

        if ask_TimeIncrement or ask_PhysicalSizes:
            okAllButton = QPushButton('Apply to ALL Positions')
            okAllButton.setToolTip(
                'Update existing Physical Sizes, Time interval, cell volume (fl), '
                'cell area (um^2), and time (s) for all the positions '
                'in the experiment folder.'
            )
            self.okAllButton = okAllButton

            selectButton = QPushButton('Select the Positions to be updated')
            selectButton.setToolTip(
                'Ask to select positions then update existing Physical Sizes, '
                'Time interval, cell volume (fl), cell area (um^2), and time (s)'
                'for selected positions.'
            )
            self.selectButton = selectButton
        else:
            self.okAllButton = None
            self.selectButton = None
            okButton.setText('Ok')

        cancelButton = acdc_widgets.cancelPushButton('Cancel')

        buttonsLayout.addWidget(okButton, 0, 0)
        if ask_TimeIncrement or ask_PhysicalSizes:
            buttonsLayout.addWidget(okAllButton, 0, 1)
            buttonsLayout.addWidget(selectButton, 1, 0)
            buttonsLayout.addWidget(cancelButton, 1, 1)
        else:
            buttonsLayout.addWidget(cancelButton, 0, 1)
        buttonsLayout.setContentsMargins(0, 10, 0, 0)

        if imgDataShape is not None:
            label = QLabel(html_func.paragraph(
                    f'<i>Image data shape</i> = <b>{imgDataShape}</b><br>'
                )
            )
            mainLayout.addWidget(label, alignment=Qt.AlignCenter)
        mainLayout.addWidget(loadingSizesGroupbox)
        mainLayout.addStretch(1)
        mainLayout.addSpacing(10)
        mainLayout.addWidget(metadataGroupbox)
        mainLayout.addLayout(buttonsLayout)

        okButton.clicked.connect(self.ok_cb)
        if ask_TimeIncrement or ask_PhysicalSizes:
            okAllButton.clicked.connect(self.ok_cb)
            selectButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)

        self.setLayout(mainLayout)

    def SizeZvalueChanged(self, val):
        if len(self.imgDataShape) < 3:
            return
        if val > 1 and self.imgDataShape is not None:
            maxSizeZ = self.imgDataShape[-3]
            self.SizeZ_SpinBox.setMaximum(maxSizeZ)
            if self.fileExt == '.h5':
                self.loadSizeZ_SpinBox.setDisabled(False)
        else:
            self.SizeZ_SpinBox.setMaximum(2147483647)
            self.loadSizeZ_SpinBox.setValue(1)
            self.loadSizeZ_SpinBox.setDisabled(True)

        if not self.ask_PhysicalSizes:
            return
        if val > 1:
            self.PhysicalSizeZSpinBox.show()
            self.PhysicalSizeZLabel.show()
        else:
            self.PhysicalSizeZSpinBox.hide()
            self.PhysicalSizeZLabel.hide()

    def TimeIncrementShowHide(self, val):
        if not self.ask_TimeIncrement:
            return
        if val > 1:
            self.TimeIncrementSpinBox.show()
            self.TimeIncrementLabel.show()
            if self.fileExt == '.h5':
                self.loadSizeT_SpinBox.setDisabled(False)
        else:
            self.TimeIncrementSpinBox.hide()
            self.TimeIncrementLabel.hide()
            self.loadSizeT_SpinBox.setDisabled(True)
            self.loadSizeT_SpinBox.setValue(1)

    def ok_cb(self, event):
        self.cancel = False
        self.SizeT = self.SizeT_SpinBox.value()
        self.SizeZ = self.SizeZ_SpinBox.value()

        self.loadSizeS = self.loadSizeS_SpinBox.value()
        self.loadSizeT = self.loadSizeT_SpinBox.value()
        self.loadSizeZ = self.loadSizeZ_SpinBox.value()
        self.TimeIncrement = self.TimeIncrementSpinBox.value()
        self.PhysicalSizeX = self.PhysicalSizeXSpinBox.value()
        self.PhysicalSizeY = self.PhysicalSizeYSpinBox.value()
        self.PhysicalSizeZ = self.PhysicalSizeZSpinBox.value()
        valid4D = True
        valid3D = True
        valid2D = True
        if self.imgDataShape is None:
            self.close()
        elif len(self.imgDataShape) == 4:
            T, Z, Y, X = self.imgDataShape
            valid4D = self.SizeT == T and self.SizeZ == Z
        elif len(self.imgDataShape) == 3:
            TZ, Y, X = self.imgDataShape
            valid3D = self.SizeT == TZ or self.SizeZ == TZ
        elif len(self.imgDataShape) == 2:
            valid2D = self.SizeT == 1 and self.SizeZ == 1
        valid = all([valid4D, valid3D, valid2D])
        if not valid4D:
            txt = html_func.paragraph(
                'You loaded <b>4D data</b>, hence the number of frames MUST be '
                f'<b>{T}</b><br> nd the number of z-slices MUST be <b>{Z}</b>.'
                '<br><br> What do you want to do?'
            )
        if not valid3D:
            txt = html_func.paragraph(
                'You loaded <b>3D data</b>, hence either the number of frames is '
                f'<b>{TZ}</b><br> or the number of z-slices can be <b>{TZ}</b>.<br><br>'
                'However, if the number of frames is greater than 1 then the<br>'
                'number of z-slices MUST be 1, and vice-versa.<br><br>'
                'What do you want to do?'
            )

        if not valid2D:
            txt = html_func.paragraph(
                'You loaded <b>2D data</b>, hence the number of frames MUST be <b>1</b> '
                'and the number of z-slices MUST be <b>1</b>.<br><br>'
                'What do you want to do?'
            )

        if not valid:
            msg = acdc_widgets.myMessageBox(self)
            continueButton, cancelButton = msg.warning(
                self, 'Invalid entries', txt,
                buttonsTexts=('Continue', 'Let me correct')
            )
            if msg.clickedButton == cancelButton:
                return

        if self.PosData is not None and self.sender() != self.okButton:
            exp_path = self.PosData.exp_path
            pos_foldernames = natsorted(utils.listdir(exp_path))
            pos_foldernames = [
                pos for pos in pos_foldernames
                if pos.find('Position_')!=-1
                and os.path.isdir(os.path.join(exp_path, pos))
            ]
            if self.sender() == self.selectButton:
                select_folder = io.select_exp_folder()
                select_folder.pos_foldernames = pos_foldernames
                select_folder.QtPrompt(
                    self, pos_foldernames, allow_abort=False, toggleMulti=True
                )
                pos_foldernames = select_folder.selected_pos
            for pos in pos_foldernames:
                images_path = os.path.join(exp_path, pos, 'Images')
                ls = utils.listdir(images_path)
                search = [file for file in ls if file.find('metadata.csv')!=-1]
                metadata_df = None
                if search:
                    fileName = search[0]
                    metadata_csv_path = os.path.join(images_path, fileName)
                    metadata_df = pd.read_csv(
                        metadata_csv_path
                        ).set_index('Description')
                if metadata_df is not None:
                    metadata_df.at['TimeIncrement', 'values'] = self.TimeIncrement
                    metadata_df.at['PhysicalSizeZ', 'values'] = self.PhysicalSizeZ
                    metadata_df.at['PhysicalSizeY', 'values'] = self.PhysicalSizeY
                    metadata_df.at['PhysicalSizeX', 'values'] = self.PhysicalSizeX
                    metadata_df.to_csv(metadata_csv_path)

                search = [file for file in ls if file.find('acdc_output.csv')!=-1]
                acdc_df = None
                if search:
                    fileName = search[0]
                    acdc_df_path = os.path.join(images_path, fileName)
                    acdc_df = pd.read_csv(acdc_df_path)
                    yx_pxl_to_um2 = self.PhysicalSizeY*self.PhysicalSizeX
                    vox_to_fl = self.PhysicalSizeY*(self.PhysicalSizeX**2)
                    if 'cell_vol_fl' not in acdc_df.columns:
                        continue
                    acdc_df['cell_vol_fl'] = acdc_df['cell_vol_vox']*vox_to_fl
                    acdc_df['cell_area_um2'] = acdc_df['cell_area_pxl']*yx_pxl_to_um2
                    acdc_df['time_seconds'] = acdc_df['frame_i']*self.TimeIncrement
                    try:
                        acdc_df.to_csv(acdc_df_path, index=False)
                    except PermissionError:
                        err_msg = (
                            'The below file is open in another app '
                            '(Excel maybe?).\n\n'
                            f'{acdc_df_path}\n\n'
                            'Close file and then press "Ok".'
                        )
                        msg = acdc_widgets.myMessageBox()
                        msg.critical(self, 'Permission denied', err_msg)
                        acdc_df.to_csv(acdc_df_path, index=False)

        elif self.sender() == self.selectButton:
            pass

        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.close()

class QDialogCombobox(QBaseDialog):
    def __init__(
            self, title, ComboBoxItems, informativeText,
            CbLabel='Select value:  ', parent=None,
            defaultChannelName=None, iconPixmap=None
        ):
        self.cancel = True
        self.selectedItemText = ''
        self.selectedItemIdx = None
        super().__init__(parent)
        self.setWindowTitle(title)

        mainLayout = QVBoxLayout()
        infoLayout = QHBoxLayout()
        topLayout = QHBoxLayout()
        bottomLayout = QHBoxLayout()

        if iconPixmap is not None:
            label = QLabel()
            # padding: top, left, bottom, right
            # label.setStyleSheet("padding:5px 0px 10px 0px;")
            label.setPixmap(iconPixmap)
            infoLayout.addWidget(label)

        if informativeText:
            infoLabel = QLabel(informativeText)
            infoLayout.addWidget(infoLabel, alignment=Qt.AlignCenter)

        if CbLabel:
            label = QLabel(CbLabel)
            topLayout.addWidget(label, alignment=Qt.AlignRight)

        combobox = QComboBox()
        combobox.addItems(ComboBoxItems)
        if defaultChannelName is not None and defaultChannelName in ComboBoxItems:
            combobox.setCurrentText(defaultChannelName)
        self.ComboBox = combobox
        topLayout.addWidget(combobox)
        topLayout.setContentsMargins(0, 10, 0, 0)

        okButton = acdc_widgets.okPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        bottomLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = acdc_widgets.cancelPushButton('Cancel')
        bottomLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        bottomLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(infoLayout)
        mainLayout.addLayout(topLayout)
        mainLayout.addLayout(bottomLayout)
        self.setLayout(mainLayout)

        # Connect events
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.close)


    def ok_cb(self, event):
        self.cancel = False
        self.selectedItemText = self.ComboBox.currentText()
        self.selectedItemIdx = self.ComboBox.currentIndex()
        self.close()

class QDialogListbox(QBaseDialog):
    def __init__(
            self, title, text, items, moreButtonFuncText='Cancel',
            multiSelection=True, currentItem=None,
            filterItems=(), parent=None
        ):
        self.cancel = True
        super().__init__(parent)
        self.setWindowTitle(title)

        mainLayout = QVBoxLayout()
        topLayout = QVBoxLayout()
        bottomLayout = QHBoxLayout()

        label = QLabel(text)

        label.setFont(font)
        # padding: top, left, bottom, right
        label.setStyleSheet("padding:0px 0px 3px 0px;")
        topLayout.addWidget(label, alignment=Qt.AlignCenter)

        if filterItems:
            filteredItems = []
            for item in items:
                for textToFind in filterItems:
                    if item.find(textToFind) != -1:
                        filteredItems.append(item)
            items = filteredItems

        listBox = acdc_widgets.listWidget()
        listBox.setFont(font)
        listBox.addItems(items)
        if multiSelection:
            listBox.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        else:
            listBox.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        if currentItem is None:
            listBox.setCurrentRow(0)
        else:
            listBox.setCurrentItem(currentItem)
        self.listBox = listBox
        listBox.itemDoubleClicked.connect(self.ok_cb)
        topLayout.addWidget(listBox)

        okButton = acdc_widgets.okPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        bottomLayout.addWidget(okButton, alignment=Qt.AlignRight)

        moreButton = QPushButton(moreButtonFuncText)
        # cancelButton.setShortcut(Qt.Key_Escape)
        bottomLayout.addWidget(moreButton, alignment=Qt.AlignLeft)
        bottomLayout.setContentsMargins(0, 10, 0, 0)

        mainLayout.addLayout(topLayout)
        mainLayout.addLayout(bottomLayout)
        self.setLayout(mainLayout)

        # Connect events
        okButton.clicked.connect(self.ok_cb)
        if moreButtonFuncText.lower().find('cancel') != -1:
            moreButton.clicked.connect(self.cancel_cb)
        elif moreButtonFuncText.lower().find('browse') != -1:
            moreButton.clicked.connect(self.browse)

        listBox.setFocus()
        self.setMyStyleSheet()

    def setMyStyleSheet(self):
        self.setStyleSheet("""
            QListWidget::item:hover {background-color:#E6E6E6;}
            QListWidget::item:hover {color:black;}
            QListWidget::item:selected {
                background-color:#CFEB9B;
                color:black;
                border-left:none;
                border-top:none;
                border-right:none;
                border-bottom:none;
            }
            QListWidget::item {padding: 5px;}
            QListView  {
                selection-background-color: #CFEB9B;
                show-decoration-selected: 1;
                outline: 0;
            }
        """)

    def browse(self, event):
        pass

    def ok_cb(self, event):
        self.cancel = False
        selectedItems = self.listBox.selectedItems()
        self.selectedItems = selectedItems
        self.selectedItemsText = [item.text() for item in selectedItems]
        self.close()

    def cancel_cb(self, event):
        self.cancel = True
        self.selectedItemsText = None
        self.close()

class selectedPathsSummaryDialog(acdc_apps.TreeSelectorDialog):
    def __init__(self) -> None:
        super().__init__()

class selectPathsSpotmax(QBaseDialog):
    def __init__(self, paths, homePath, parent=None, app=None):
        super().__init__(parent)

        self.cancel = True

        self.selectedPaths = []
        self.paths = paths
        runs = sorted(list(self.paths.keys()))
        self.runs = runs

        self.setWindowTitle('Select experiments to load/analyse')

        infoLabel = QLabel()
        text = (
            'Select <b>one or more folders</b> to load<br><br>'
            '<code>Click</code> on experiment path <i>to select all positions</i><br>'
            '<code>Ctrl+Click</code> <i>to select multiple items</i><br>'
            '<code>Shift+Click</code> <i>to select a range of items</i><br>'
            '<code>Ctrl+A</code> <i>to select all</i><br>'
        )
        htmlText = html_func.paragraph(text, center=True)
        infoLabel.setText(htmlText)

        runNumberLayout = QHBoxLayout()
        runNumberLabel = QLabel()
        text = 'Number of pos. analysed for run number: '
        htmlText = html_func.paragraph(text)
        runNumberLabel.setText(htmlText)
        runNumberCombobox = QComboBox()
        runNumberCombobox.addItems([f'  {r}  ' for r in runs])
        runNumberCombobox.setCurrentIndex(len(runs)-1)
        self.runNumberCombobox = runNumberCombobox
        showAnalysisTableButton = widgets.showPushButton(
            'Show analysis inputs for selected run and selected experiment'
        )

        runNumberLayout.addStretch(1)
        runNumberLayout.addWidget(runNumberLabel, alignment=Qt.AlignRight)
        runNumberLayout.addWidget(runNumberCombobox, alignment=Qt.AlignRight)
        runNumberLayout.addWidget(showAnalysisTableButton)
        runNumberLayout.addStretch(1)

        checkBoxesLayout = QHBoxLayout()
        hideSpotCountCheckbox = QCheckBox('Hide fully spotCOUNTED')
        hideSpotSizeCheckbox = QCheckBox('Hide fully spotSIZED')
        checkBoxesLayout.addStretch(1)
        checkBoxesLayout.addWidget(
            hideSpotCountCheckbox, alignment=Qt.AlignCenter
        )
        checkBoxesLayout.addWidget(
            hideSpotSizeCheckbox, alignment=Qt.AlignCenter
        )
        checkBoxesLayout.addStretch(1)
        self.hideSpotCountCheckbox = hideSpotCountCheckbox
        self.hideSpotSizeCheckbox = hideSpotSizeCheckbox

        pathSelector = acdc_widgets.TreeWidget()
        self.pathSelector = pathSelector
        pathSelector.setHeaderHidden(True)
        homePath = pathlib.Path(homePath)
        self.homePath = homePath
        self.populatePathSelector()

        buttonsLayout = QHBoxLayout()
        cancelButton = acdc_widgets.cancelPushButton('Cancel')
        buttonsLayout.addStretch(1)
        buttonsLayout.addWidget(cancelButton)
        buttonsLayout.addSpacing(20)

        showInFileManagerButton = acdc_widgets.showInFileManagerButton(
            setDefaultText=True
        )
        showInFileManagerButton.clicked.connect(self.showInFileManager)
        buttonsLayout.addWidget(showInFileManagerButton)

        okButton = acdc_widgets.okPushButton('Ok')
        # okButton.setShortcut(Qt.Key_Enter)
        buttonsLayout.addWidget(okButton)

        mainLayout = QVBoxLayout()
        mainLayout.addWidget(infoLabel, alignment=Qt.AlignCenter)
        mainLayout.addLayout(runNumberLayout)
        runNumberLayout.setContentsMargins(0, 0, 0, 10)
        mainLayout.addLayout(checkBoxesLayout)
        mainLayout.addWidget(pathSelector)
        mainLayout.addSpacing(20)
        mainLayout.addLayout(buttonsLayout)
        self.setLayout(mainLayout)

        hideSpotCountCheckbox.stateChanged.connect(self.hideSpotCounted)
        hideSpotSizeCheckbox.stateChanged.connect(self.hideSpotSized)
        runNumberCombobox.currentIndexChanged.connect(self.updateRun)
        showAnalysisTableButton.clicked.connect(self.showAnalysisInputsTable)
        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.cancel_cb)
        pathSelector.itemClicked.connect(self.selectAllChildren)

        self.pathSelector.setFocus()

        self.setFont(font)
    
    def showInFileManager(self):
        selectedItems = self.pathSelector.selectedItems()
        doc = QTextDocument()
        firstItem = selectedItems[0]
        label = self.pathSelector.itemWidget(firstItem, 0)
        doc.setHtml(label.text())
        plainText = doc.toPlainText()
        parent = firstItem.parent()
        if parent is None:
            posFoldername = ''
            parentText = plainText
        else:
            try:
                posFoldername = re.findall('(.+) \(', plainText)[0]
            except IndexError:
                posFoldername = plainText
            parentLabel = self.pathSelector.itemWidget(parent, 0)
            doc.setHtml(parentLabel.text())
            parentText = doc.toPlainText()
        
        relPath = re.findall('...(.+) \(', parentText)[0]
        relPath = pathlib.Path(relPath)
        relPath = pathlib.Path(*relPath.parts[2:])
        absPath = self.homePath / relPath / posFoldername
        acdc_myutils.showInExplorer(str(absPath))

    def showAnalysisInputsTable(self):
        idx = self.runNumberCombobox.currentIndex()
        run = self.runs[idx]

        selectedItems = self.pathSelector.selectedItems()

        if not selectedItems:
            self.warnNoPathSelected()
            return

        doc = QTextDocument()
        item = selectedItems[0]
        text = item.text(0)
        doc.setHtml(text)
        plainText = doc.toPlainText()
        parent = item.parent()
        if parent is None:
            relPath1 = re.findall('...(.+) \(', plainText)[0]
            relPath1 = pathlib.Path(relPath1)
            relPath = pathlib.Path(*relPath1.parts[2:])
            if str(relPath) == '.':
                relPath = ''
            exp_path = os.path.join(self.homePath, relPath)

            selectedRunPaths = self.paths[run]
            analysisInputs = selectedRunPaths[os.path.normpath(exp_path)].get(
                'analysisInputs'
            )
        else:
            posFoldername = re.findall('(.+) \(', plainText)[0]
            doc.setHtml(parent.text(0))
            parentText = doc.toPlainText()
            relPath1 = re.findall('...(.+) \(', parentText)[0]
            relPath1 = pathlib.Path(relPath1)
            relPath = pathlib.Path(*relPath1.parts[2:])
            relPath1 = relPath / posFoldername
            exp_path = self.homePath / relPath / posFoldername
            spotmaxOutPath = exp_path / 'spotMAX_output'
            if os.path.exists(spotmaxOutPath):
                analysisInputs = io.expFolderScanner().loadAnalysisInputs(
                    spotmaxOutPath, run
                )
            else:
                analysisInputs = None

        if analysisInputs is None:
            self.warnAnalysisInputsNone(exp_path, run)
            return

        if isinstance(analysisInputs, pd.DataFrame):
            title = f'Analysis inputs table'
            infoText = html_func.paragraph(
                f'Analysis inputs used to analyse <b>run number {run}</b> '
                f'of experiment:<br>"{relPath1}"<br>'
            )
            self.analysisInputsTableWin = pdDataFrameWidget(
                analysisInputs.reset_index(), title=title, infoText=infoText, 
                parent=self
            )
        else:
            self.analysisInputsTableWin = iniFileWidget(
                analysisInputs, filename=analysisInputs.filename()
            )
        self.analysisInputsTableWin.show()

    def updateRun(self, idx):
        self.pathSelector.clear()
        self.populatePathSelector()
        self.resizeSelector()

    def populatePathSelector(self):
        addSpotCounted = not self.hideSpotCountCheckbox.isChecked()
        addSpotSized = not self.hideSpotSizeCheckbox.isChecked()
        pathSelector = self.pathSelector
        idx = self.runNumberCombobox.currentIndex()
        run = self.runs[idx]
        selectedRunPaths = self.paths[run]
        relPathItem = None
        posItem = None
        for exp_path, expInfo in selectedRunPaths.items():
            exp_path = pathlib.Path(exp_path)
            rel = exp_path.relative_to(self.homePath)
            if str(rel) == '.':
                rel = ''
            relPath = (
                f'...{self.homePath.parent.name}{os.path.sep}'
                f'{self.homePath.name}{os.path.sep}{rel}'
            )

            numPosSpotCounted = expInfo['numPosSpotCounted']
            numPosSpotSized = expInfo['numPosSpotSized']
            posFoldernames = expInfo['posFoldernames']
            totPos = len(posFoldernames)
            if numPosSpotCounted < totPos and numPosSpotCounted>0:
                nPSCtext = f'N. of spotCOUNTED pos. = {numPosSpotCounted}'
            elif numPosSpotCounted>0:
                nPSCtext = f'All pos. spotCOUNTED'
                if not addSpotCounted:
                    continue
            else:
                nPSCtext = 'Never spotCOUNTED'

            if numPosSpotSized < totPos and numPosSpotSized>0:
                nPSStext = f'Number of spotSIZED pos. = {numPosSpotSized}'
            elif numPosSpotSized>0:
                nPSStext = f'All pos. spotSIZED'
                if not addSpotSized:
                    continue
            elif numPosSpotCounted>0:
                nPSStext = 'NONE of the pos. spotSIZED'
            else:
                nPSStext = 'Never spotSIZED'

            relPathItem = QTreeWidgetItem()
            pathSelector.addTopLevelItem(relPathItem)
            relPathText = f'{relPath} ({nPSCtext}, {nPSStext})'
            relPathItem.setText(0, relPathText)
            
            # relPathLabel = acdc_widgets.QClickableLabel()
            # relPathLabel.item = relPathItem
            # relPathLabel.clicked.connect(self.selectAllChildren)

            for pos in posFoldernames:
                posInfo = expInfo[pos]
                isPosSpotCounted = posInfo['isPosSpotCounted']
                isPosSpotSized = posInfo['isPosSpotSized']
                posText = pos
                if isPosSpotCounted and isPosSpotSized:
                    posText = f'{posText} (spotCOUNTED, spotSIZED)'
                    if not addSpotSized or not addSpotCounted:
                        continue
                elif isPosSpotCounted:
                    posText = f'{posText} (spotCOUNTED, NOT spotSIZED)'
                    if not addSpotCounted:
                        continue
                else:
                    posText = f'{posText} (NOT spotCOUNTED, NOT spotSIZED)'
                posItem = QTreeWidgetItem()
                posItem.setText(0, posText)
                # posLabel = acdc_widgets.QClickableLabel()
                # posLabel.item = posItem
                # posLabel.clicked.connect(self.selectAllChildren)
                # posLabel.setText(posText)
                relPathItem.addChild(posItem)
                # pathSelector.setItemWidget(posItem, 0, posLabel)
        if relPathItem is not None and len(selectedRunPaths) == 1:
            relPathItem.setExpanded(True)

    def selectAllChildren(self, label=None):
        self.pathSelector.selectAllChildren(label)
    
    def warnAnalysisInputsNone(self, exp_path, run):
        text = (
            f'The selected experiment "{exp_path}" '
            f'does not have the <b>"{run}_analysis_inputs.csv"</b> nor '
            f'the <b>"{run}_analysis_parameters.ini"</b> file.<br><br>'
            'Sorry about that.'
        )
        msg = acdc_widgets.myMessageBox()
        msg.addShowInFileManagerButton(exp_path)
        msg.warning(
            self, 'Analysis inputs not found!',
            html_func.paragraph(text)
        )

    def ok_cb(self, checked=True):
        selectedItems = self.pathSelector.selectedItems()
        doc = QTextDocument()
        for item in selectedItems:
            plainText = item.text(0)
            parent = item.parent()
            if parent is None:
                continue
            try:
                posFoldername = re.findall('(.+) \(', plainText)[0]
            except IndexError:
                posFoldername = plainText
            parentText = parent.text(0)
            relPath = re.findall('...(.+) \(', parentText)[0]
            relPath = pathlib.Path(relPath)
            relPath = pathlib.Path(*relPath.parts[2:])
            absPath = self.homePath / relPath / posFoldername
            imagesPath = absPath / 'Images'
            self.selectedPaths.append(imagesPath)

        doClose = True
        if not self.selectedPaths:
            doClose = self.warningNotPathsSelected()

        if doClose:
            self.close()

    def warnNoPathSelected(self):
        text = (
            'You didn\'t select <b>any experiment path!</b><br><br>'
            'To visualize the analysis inputs I need to know '
            'the experiment path you want me to show you.<br><br>'
            '<i>Note that if you select multiple experiments I will show you '
            'only the first one that you selected.</i>'
        )
        msg = acdc_widgets.myMessageBox()
        msg.warning(
            self, 'No path selected!', html_func.paragraph(text)
        )

    def warningNotPathsSelected(self):
        text = (
            '<b>You didn\'t select any path!</b> Do you want to cancel loading data?'
        )
        msg = acdc_widgets.myMessageBox()
        doClose, _ = msg.warning(
            self, 'No paths selected!', html_func.paragraph(text),
            buttonsTexts=(' Yes ', 'No')
        )
        return msg.clickedButton==doClose

    def cancel_cb(self, event):
        self.close()

    def hideSpotCounted(self, state):
        self.pathSelector.clear()
        self.populatePathSelector()

    def hideSpotSized(self, state):
        self.pathSelector.clear()
        self.populatePathSelector()

    def resizeSelector(self):
        w = 0
        for i in range(self.pathSelector.topLevelItemCount()):
            item = self.pathSelector.topLevelItem(i)
            labelText = item.text(0)
            currentW = item.sizeHint(0).width()
            if currentW > w:
                w = currentW

        self.pathSelector.setMinimumWidth(w)

    def show(self, block=False):
        super().show(block=False)
        self.resizeSelector()
        if block:
            super().show(block=True)

class DataFrameModel(QtCore.QAbstractTableModel):
    # https://stackoverflow.com/questions/44603119/how-to-display-a-pandas-data-frame-with-pyqt5-pyside2
    DtypeRole = QtCore.Qt.UserRole + 1000
    ValueRole = QtCore.Qt.UserRole + 1001

    def __init__(self, df=pd.DataFrame(), parent=None):
        super(DataFrameModel, self).__init__(parent)
        self._dataframe = df

    def setDataFrame(self, dataframe):
        self.beginResetModel()
        self._dataframe = dataframe.copy()
        self.endResetModel()

    def dataFrame(self):
        return self._dataframe

    dataFrame = QtCore.Property(pd.DataFrame, fget=dataFrame,
                                    fset=setDataFrame)

    @QtCore.Slot(int, QtCore.Qt.Orientation, result=str)
    def headerData(self, section: int,
                   orientation: QtCore.Qt.Orientation,
                   role: int = QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole:
            if orientation == QtCore.Qt.Horizontal:
                return self._dataframe.columns[section]
            else:
                return str(self._dataframe.index[section])
        return QtCore.QVariant()

    def rowCount(self, parent=QtCore.QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._dataframe.index)

    def columnCount(self, parent=QtCore.QModelIndex()):
        if parent.isValid():
            return 0
        return self._dataframe.columns.size

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < self.rowCount() \
            and 0 <= index.column() < self.columnCount()):
            return QtCore.QVariant()
        row = self._dataframe.index[index.row()]
        col = self._dataframe.columns[index.column()]
        dt = self._dataframe[col].dtype

        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter

        val = self._dataframe.iloc[row][col]
        if role == QtCore.Qt.DisplayRole:
            return str(val)
        elif role == DataFrameModel.ValueRole:
            return val
        if role == DataFrameModel.DtypeRole:
            return dt
        return QtCore.QVariant()

    def roleNames(self):
        roles = {
            QtCore.Qt.DisplayRole: b'display',
            DataFrameModel.DtypeRole: b'dtype',
            DataFrameModel.ValueRole: b'value'
        }
        return roles

class iniFileWidget(QBaseDialog):
    def __init__(self, configPars, filename='', parent=None):
        self.cancel = True

        super().__init__(parent)

        self.setWindowTitle('Configuration file content')

        mainLayout = QVBoxLayout()

        if filename:
            label = QLabel()
            txt = html_func.paragraph(f'Filename: <code>{filename}</code><br>')
            label.setText(txt)
            mainLayout.addWidget(label)
        
        self.textWidget = QTextEdit()
        self.textWidget.setReadOnly(True)
        self.setIniText(configPars)
        
        buttonsLayout = QHBoxLayout()
        buttonsLayout.addStretch(1)

        okButton = acdc_widgets.okPushButton(' Ok ')
        buttonsLayout.addWidget(okButton)

        okButton.clicked.connect(self.ok_cb)
        
        mainLayout.addWidget(self.textWidget)
        mainLayout.addLayout(buttonsLayout)
        self.setLayout(mainLayout)
    
    def setIniText(self, configPars):
        htmlText = ''
        palette = palettes.ini_hex_colors()
        section_hex = palette['section']
        option_hex = palette['option']
        for section in configPars.sections():
            sectionText = html_func.span(f'[{section}]', font_color=section_hex)
            htmlText = f'{htmlText}{sectionText}<br>'
            for option in configPars.options(section):
                value = configPars[section][option]
                # option = option.replace('Î¼', '&micro;')
                optionText = html_func.span(
                    f'<i>{option}</i> = ', font_color=option_hex
                )
                value = value.replace('\n', '<br>&nbsp;&nbsp;&nbsp;&nbsp;')
                htmlText = f'{htmlText}{optionText}{value}<br>'
            htmlText = f'{htmlText}<br>'
        self.textWidget.setHtml(html_func.paragraph(htmlText))
    
    def show(self, block=False):
        super().show(block=False)
        self.move(self.pos().x(), 20)
        height = int(self.screen().size().height()*0.7)
        width = round(height*0.85)
        self.resize(width, height)
        super().show(block=block)
    
    def ok_cb(self):
        self.cancel = False
        self.close()

class pdDataFrameWidget(QMainWindow):
    def __init__(self, df, title='Table', infoText='', parent=None):
        super().__init__(parent)
        self.parent = parent
        self.setWindowTitle(title)

        mainContainer = QWidget()
        self.setCentralWidget(mainContainer)

        layout = QVBoxLayout()

        if infoText:
            infoLabel = QLabel(infoText)
            infoLabel.setAlignment(Qt.AlignCenter)
            layout.addWidget(infoLabel)

        self.tableView = QTableView(self)
        layout.addWidget(self.tableView)
        model = DataFrameModel(df)
        self.tableView.setModel(model)
        for i in range(len(df.columns)):
            self.tableView.resizeColumnToContents(i)
        mainContainer.setLayout(layout)

    def updateTable(self, df):
        if df is None:
            df = self.parent.getBaseCca_df()
        df = df.reset_index()
        model = DataFrameModel(df)
        self.tableView.setModel(model)
        for i in range(len(df.columns)):
            self.tableView.resizeColumnToContents(i)

    def show(self, maxWidth=1024):
        QMainWindow.show(self)


        width = self.tableView.verticalHeader().width() + 28
        for j in range(self.tableView.model().columnCount()):
            width += self.tableView.columnWidth(j) + 4

        height = self.tableView.horizontalHeader().height() + 4
        h = height + (self.tableView.rowHeight(0) + 4)*15
        w = width if width<maxWidth else maxWidth
        self.setGeometry(100, 100, w, h)

        # Center window
        parent = self.parent
        if parent is not None:
            # Center the window on main window
            mainWinGeometry = parent.geometry()
            mainWinLeft = mainWinGeometry.left()
            mainWinTop = mainWinGeometry.top()
            mainWinWidth = mainWinGeometry.width()
            mainWinHeight = mainWinGeometry.height()
            mainWinCenterX = int(mainWinLeft + mainWinWidth/2)
            mainWinCenterY = int(mainWinTop + mainWinHeight/2)
            winGeometry = self.geometry()
            winWidth = winGeometry.width()
            winHeight = winGeometry.height()
            winLeft = int(mainWinCenterX - winWidth/2)
            winRight = int(mainWinCenterY - winHeight/2)
            self.move(winLeft, winRight)

    def closeEvent(self, event):
        self.parent.ccaTableWin = None

class selectSpotsH5FileDialog(QBaseDialog):
    def __init__(self, runsInfo, parent=None, app=None):
        QDialog.__init__(self, parent)

        self.setWindowTitle('Select analysis to load')

        self.parent = parent
        self.app = app
        self.runsInfo = runsInfo
        self.selectedFile = None

        self.setFont(font)

        mainLayout = selectSpotsH5FileLayout(
            runsInfo, font=font, parent=self, app=app
        )

        buttonsLayout = QHBoxLayout()
        okButton = acdc_widgets.okPushButton('Ok')
        okButton.setShortcut(Qt.Key_Enter)
        buttonsLayout.addWidget(okButton, alignment=Qt.AlignRight)

        cancelButton = acdc_widgets.cancelPushButton('Cancel')
        buttonsLayout.addWidget(cancelButton, alignment=Qt.AlignLeft)
        buttonsLayout.setContentsMargins(0, 20, 0, 0)
        mainLayout.addLayout(buttonsLayout)

        okButton.clicked.connect(self.ok_cb)
        cancelButton.clicked.connect(self.close)

        self.mainLayout = mainLayout
        self.setLayout(mainLayout)

        self.setMyStyleSheet()

    def setMyStyleSheet(self):
        self.setStyleSheet("""
            QTreeWidget::item:hover {background-color:#E6E6E6;}
            QTreeWidget::item:hover {color:black;}
            QTreeWidget::item:selected {
                background-color:#CFEB9B;
                color:black;
            }
            QTreeView {
                selection-background-color: #CFEB9B;
                show-decoration-selected: 1;
                outline: 0;
            }
            QTreeWidget::item {padding: 5px;}
        """)

    def ok_cb(self, checked=True):
        selectedItems = self.mainLayout.treeSelector.selectedItems()
        if not selectedItems:
            doClose = self.warningNoFilesSelected()
            if doClose:
                self.close()
            return
        self.cancel = False
        selectedItem = selectedItems[0]
        runItem = selectedItem.parent()
        runNumber = int(re.findall('(\d+)', runItem.text(0))[0])
        idx = selectedItem.parent().indexOfChild(selectedItem)
        self.selectedFile = self.runsInfo[runNumber][idx]
        self.close()

    def warningNoFilesSelected(self):
        text = (
            'You didn\'t select <b>any analysis run!</b><br><br>'
            'Do you want to cancel the process?'
        )
        msg = acdc_widgets.myMessageBox()
        doClose, _ = msg.warning(
            self, 'No files selected!', html_func.paragraph(text),
            buttonsTexts=(' Yes ', 'No')
        )
        return msg.clickedButton==doClose

    def cancel_cb(self, checked=True):
        self.close()

    def resizeSelector(self):
        longestText = '3: Spots after goodness-of-peak AND ellipsoid test'
        w = (
            QFontMetrics(self.font())
            .boundingRect(longestText)
            .width()+120
        )
        self.mainLayout.treeSelector.setMinimumWidth(w)

    def show(self, block=False):
        super().show(block=False)
        self.resizeSelector()
        if block:
            super().show(block=True)

class selectSpotsH5FileLayout(QVBoxLayout):
    def __init__(self, runsInfo, font=None, parent=None, app=None):
        super().__init__(parent)
        self.runsInfo = runsInfo
        self.selectedFile = None
        self.font = font

        infoLabel = QLabel()
        text = 'Select which analysis to load <br>'
        htmlText = html_func.paragraph(text)
        infoLabel.setText(htmlText)

        treeSelector = QTreeWidget()
        self.treeSelector = treeSelector
        treeSelector.setHeaderHidden(True)
        self.populateSelector()

        self.addWidget(infoLabel, alignment=Qt.AlignCenter)
        self.addWidget(treeSelector)
        treeSelector.itemClicked.connect(self.expandTopLevel)

        treeSelector.setFocus()

    def populateSelector(self):
        for run, files in self.runsInfo.items():
            runItem = QTreeWidgetItem(self.treeSelector)
            runItem.setText(0, f'Analysis run number {run}')
            if self.font is not None:
                runItem.setFont(0, self.font)
            self.treeSelector.addTopLevelItem(runItem)
            for file in files:
                if file.find('0_Orig_data') != -1:
                    txt = '0: All detected spots'
                elif file.find('1_ellip_test') != -1:
                    txt = '1: Spots after ellipsoid test'
                elif file.find('2_p-_test') != -1:
                    txt = '2: Spots after goodness-of-peak test'
                elif file.find('3_p-_ellip_test') != -1:
                    txt = '3: Spots after goodness-of-peak AND ellipsoid test'
                elif file.find('4_spotFIT') != -1:
                    txt = '4: Spots after size test (spotFIT)'
                fileItem = QTreeWidgetItem(runItem)
                fileItem.setText(0, txt)
                if self.font is not None:
                    fileItem.setFont(0, self.font)
                runItem.addChild(fileItem)

    def expandTopLevel(self, item):
        if item.parent() is None:
            item.setExpanded(True)
            item.setSelected(False)

def getSelectedExpPaths(utilityName, parent=None):
    msg = acdc_widgets.myMessageBox()
    txt = html_func.paragraph("""
        After you click "Ok" on this dialog you will be asked
        to <b>select the experiment folders</b>, one by one.<br><br>
        Next, you will be able to <b>choose specific Positions</b>
        from each selected experiment.
    """)
    msg.information(
        parent, f'{utilityName}', txt,
        buttonsTexts=('Cancel', 'Ok')
    )
    if msg.cancel:
        return

    expPaths = {}
    mostRecentPath = acdc_myutils.getMostRecentPath()
    while True:
        exp_path = QFileDialog.getExistingDirectory(
            parent, 'Select experiment folder containing Position_n folders',
            mostRecentPath
        )
        if not exp_path:
            break
        acdc_myutils.addToRecentPaths(exp_path)
        pathScanner = io.expFolderScanner(homePath=exp_path)
        _exp_paths = pathScanner.getExpPathsWithPosFoldernames()
        
        expPaths = {**expPaths, **_exp_paths}
        mostRecentPath = exp_path
        msg = acdc_widgets.myMessageBox(wrapText=False)
        txt = html_func.paragraph("""
            Do you want to select <b>additional experiment folders</b>?
        """)
        noButton, yesButton = msg.question(
            parent, 'Select additional experiments?', txt,
            buttonsTexts=('No', 'Yes')
        )
        if msg.clickedButton == noButton:
            break
    
    if not expPaths:
        return

    multiplePos = any([len(posFolders) > 1 for posFolders in expPaths.values()])

    if len(expPaths) > 1 or multiplePos:
        # infoPaths = io.getInfoPosStatus(expPaths)
        selectPosWin = acdc_apps.selectPositionsMultiExp(expPaths)
        selectPosWin.exec_()
        if selectPosWin.cancel:
            return
        selectedExpPaths = selectPosWin.selectedPaths
    else:
        selectedExpPaths = expPaths
    
    return selectedExpPaths

class SpotsItemPropertiesDialog(QBaseDialog):
    sigDeleteSelecAnnot = Signal(object)

    def __init__(self, df_spots_files, spotmax_out_path, parent=None, state=None):
        self.cancel = True
        self.loop = None
        self.clickedButton = None
        self.spotmax_out_path = spotmax_out_path

        super().__init__(parent)

        self.setWindowTitle('Load spots table to visualize')

        layout = acdc_widgets.FormLayout()

        row = 0
        h5fileCombobox = QComboBox()
        h5fileCombobox.addItems(df_spots_files)
        if state is not None:
            h5fileCombobox.setCurrentText(state['h5_filename'])
            h5fileCombobox.setDisabled(True)
        self.h5fileCombobox = h5fileCombobox
        body_txt = ("""
            Select which table you want to plot.
        """)
        h5FileInfoTxt = (f'{html_func.paragraph(body_txt)}')
        self.dfSpotsFileWidget = acdc_widgets.formWidget(
            h5fileCombobox, addInfoButton=True, labelTextLeft='Table to plot: ',
            parent=self, infoTxt=h5FileInfoTxt
        )
        layout.addFormWidget(self.dfSpotsFileWidget, row=row)
        self.h5fileCombobox.currentTextChanged.connect(self.setSizeFromTable)

        row += 1
        self.nameInfoLabel = QLabel()
        layout.addWidget(
            self.nameInfoLabel, row, 0, 1, 2, alignment=Qt.AlignCenter
        )

        row += 1
        symbolInfoTxt = ("""
        <b>Symbol</b> used to draw the spot.
        """)
        symbolInfoTxt = (f'{html_func.paragraph(symbolInfoTxt)}')
        self.symbolWidget = acdc_widgets.formWidget(
            acdc_widgets.pgScatterSymbolsCombobox(), addInfoButton=True,
            labelTextLeft='Symbol: ', parent=self, infoTxt=symbolInfoTxt
        )
        if state is not None:
            self.symbolWidget.widget.setCurrentText(state['symbol_text'])
        layout.addFormWidget(self.symbolWidget, row=row)

        row += 1
        shortcutInfoTxt = ("""
        <b>Shortcut</b> that you can use to <b>activate/deactivate</b> annotation
        of this spots item.<br><br> Leave empty if you don't need a shortcut.
        """)
        shortcutInfoTxt = (f'{html_func.paragraph(shortcutInfoTxt)}')
        self.shortcutWidget = acdc_widgets.formWidget(
            acdc_widgets.ShortcutLineEdit(), addInfoButton=True,
            labelTextLeft='Shortcut: ', parent=self, infoTxt=shortcutInfoTxt
        )
        if state is not None:
            self.shortcutWidget.widget.setText(state['shortcut'])
        layout.addFormWidget(self.shortcutWidget, row=row)

        row += 1
        descInfoTxt = ("""
        <b>Description</b> will be used as the <b>tool tip</b> that will be
        displayed when you hover with the mouse cursor on the toolbar button
        specific for this annotation.
        """)
        descInfoTxt = (f'{html_func.paragraph(descInfoTxt)}')
        self.descWidget = acdc_widgets.formWidget(
            QPlainTextEdit(), addInfoButton=True,
            labelTextLeft='Description: ', parent=self, infoTxt=descInfoTxt
        )
        if state is not None:
            self.descWidget.widget.setPlainText(state['description'])
        layout.addFormWidget(self.descWidget, row=row)

        row += 1
        self.colorButton = acdc_widgets.myColorButton(color=(255, 0, 0))
        self.colorButton.clicked.disconnect()
        self.colorButton.clicked.connect(self.selectColor)
        self.colorButton.setCursor(Qt.PointingHandCursor)
        self.colorWidget = acdc_widgets.formWidget(
            self.colorButton, addInfoButton=False, stretchWidget=False,
            labelTextLeft='Symbol color: ', parent=self, 
            widgetAlignment='left'
        )
        if state is not None:
            self.colorButton.setColor(state['symbolColor'])
        layout.addFormWidget(self.colorWidget, row=row)

        row += 1
        self.sizeSpinBox = acdc_widgets.SpinBox()
        self.sizeSpinBox.setMinimum(1)
        self.sizeSpinBox.setValue(3)

        self.sizeWidget = acdc_widgets.formWidget(
            self.sizeSpinBox, addInfoButton=False, stretchWidget=False,
            labelTextLeft='Symbol size: ', parent=self, 
            widgetAlignment='left'
        )
        if state is not None:
            self.sizeSpinBox.setValue(state['size'])
        layout.addFormWidget(self.sizeWidget, row=row)

        row += 1
        self.opacitySlider = acdc_widgets.sliderWithSpinBox(
            isFloat=True, normalize=True
        )
        self.opacitySlider.setMinimum(0)
        self.opacitySlider.setMaximum(100)
        self.opacitySlider.setValue(0.3)

        self.opacityWidget = acdc_widgets.formWidget(
            self.opacitySlider, addInfoButton=False, stretchWidget=True,
            labelTextLeft='Symbol opacity: ', parent=self
        )
        if state is not None:
            self.opacitySlider.setValue(state['opacity'])
        layout.addFormWidget(self.opacityWidget, row=row)

        row += 1
        layout.addItem(QSpacerItem(5, 5), row, 0)

        row += 1
        noteText = (
            '<br><i>NOTE: you can change these options later with<br>'
            '<b>RIGHT-click</b> on the associated left-side <b>toolbar button<b>.</i>'
        )
        noteLabel = QLabel(html_func.paragraph(noteText, font_size='11px'))
        layout.addWidget(noteLabel, row, 1, 1, 3)

        buttonsLayout = QHBoxLayout()

        self.okButton = acdc_widgets.okPushButton('  Ok  ')
        cancelButton = acdc_widgets.cancelPushButton('Cancel')

        buttonsLayout.addStretch(1)
        buttonsLayout.addWidget(cancelButton)
        buttonsLayout.addSpacing(20)
        buttonsLayout.addWidget(self.okButton)

        cancelButton.clicked.connect(self.cancelCallBack)
        self.cancelButton = cancelButton
        self.okButton.clicked.connect(self.ok_cb)
        self.okButton.setFocus()

        mainLayout = QVBoxLayout()

        mainLayout.addLayout(layout)
        mainLayout.addStretch(1)
        mainLayout.addSpacing(20)
        mainLayout.addLayout(buttonsLayout)

        self.setLayout(mainLayout)
        
        self.setSizeFromTable(self.h5fileCombobox.currentText())
    
    def setSizeFromTable(self, filename):
        from .core import ZYX_RESOL_COLS
        df = io.load_spots_table(self.spotmax_out_path, filename)
        try:
            size = round(df[ZYX_RESOL_COLS[1]].iloc[0]*2)
        except KeyError as err:
            return
        self.sizeSpinBox.setValue(size)

    def checkName(self, text):
        if not text:
            txt = 'Name cannot be empty'
            self.nameInfoLabel.setText(
                html_func.paragraph(
                    txt, font_size='11px', font_color='red'
                )
            )
            return
        for name in self.internalNames:
            if name.find(text) != -1:
                txt = (
                    f'"{text}" cannot be part of the name, '
                    'because <b>reserved<b>.'
                )
                self.nameInfoLabel.setText(
                    html_func.paragraph(
                        txt, font_size='11px', font_color='red'
                    )
                )
                break
        else:
            self.nameInfoLabel.setText('')

    def selectColor(self):
        color = self.colorButton.color()
        self.colorButton.origColor = color
        self.colorButton.colorDialog.setCurrentColor(color)
        self.colorButton.colorDialog.setWindowFlags(
            Qt.Window | Qt.WindowStaysOnTopHint
        )
        self.colorButton.colorDialog.open()
        w = self.width()
        left = self.pos().x()
        colorDialogTop = self.colorButton.colorDialog.pos().y()
        self.colorButton.colorDialog.move(w+left+10, colorDialogTop)

    def ok_cb(self, checked=True):
        self.cancel = False
        self.clickedButton = self.okButton
        self.toolTip = (
            f'Table name: {self.dfSpotsFileWidget.widget.currentText()}\n\n'
            f'Edit properties: right-click on button\n\n'
            f'Description: {self.descWidget.widget.toPlainText()}\n\n'
            f'SHORTCUT: "{self.shortcutWidget.widget.text()}"'
        )

        symbol = self.symbolWidget.widget.currentText()
        self.symbol = re.findall(r"\'(.+)\'", symbol)[0]

        self.state = {
            'selected_file': self.dfSpotsFileWidget.widget.currentText(),
            'symbol_text':  self.symbolWidget.widget.currentText(),
            'pg_symbol': self.symbol,
            'shortcut': self.shortcutWidget.widget.text(),
            'description': self.descWidget.widget.toPlainText(),
            'symbolColor': self.colorButton.color(),
            'size': self.sizeSpinBox.value(),
            'opacity': self.opacitySlider.value()
        }
        self.close()

    def cancelCallBack(self, checked=True):
        self.cancel = True
        self.clickedButton = self.cancelButton
        self.close()

class SelectFolderToAnalyse(QBaseDialog):
    def __init__(self, parent=None, preSelectedPaths=None):
        super().__init__(parent)
        
        self.cancel = True
        
        self.setWindowTitle('Select experiments to analyse')
        
        mainLayout = QVBoxLayout()
        
        instructionsText = html_func.paragraph(
            'Click on <code>Browse</code> button to <b>add</b> as many <b>paths</b> '
            'as needed.<br>', font_size='14px'
        )
        instructionsLabel = QLabel(instructionsText)
        instructionsLabel.setAlignment(Qt.AlignCenter)
        
        infoText = html_func.paragraph(            
            'A <b>valid folder</b> is either a <b>Position</b> folder, '
            'or an <b>experiment folder</b> (containing Position_n folders),<br>'
            'or any folder that contains <b>multiple experiment folders</b>.<br><br>'
            
            'In the last case, spotMAX will automatically scan the entire trees of '
            'sub-directories<br>'
            'and will analyse all experiments having the right folder structure.<br>',
            font_size='12px'
        )
        infoLabel = QLabel(infoText)
        infoLabel.setAlignment(Qt.AlignCenter)
        
        self.listWidget = acdc_widgets.listWidget()
        self.listWidget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        if preSelectedPaths is not None:
            self.listWidget.addItems(preSelectedPaths)
        
        buttonsLayout = acdc_widgets.CancelOkButtonsLayout()

        delButton = acdc_widgets.delPushButton('Remove selected path(s)')
        browseButton = acdc_widgets.browseFileButton(
            'Browse to add a path', openFolder=True, 
            start_dir=acdc_myutils.getMostRecentPath()
        )
        
        buttonsLayout.insertWidget(3, delButton)
        buttonsLayout.insertWidget(4, browseButton)
        
        buttonsLayout.okButton.clicked.connect(self.ok_cb)
        browseButton.sigPathSelected.connect(self.addFolderPath)
        delButton.clicked.connect(self.removePaths)
        buttonsLayout.cancelButton.clicked.connect(self.close)
        
        mainLayout.addWidget(instructionsLabel)
        mainLayout.addWidget(infoLabel)
        mainLayout.addWidget(self.listWidget)
        
        mainLayout.addSpacing(20)
        mainLayout.addLayout(buttonsLayout)
        mainLayout.addStretch(1)
        
        self.setLayout(mainLayout)
        
        font = config.font()
        self.setFont(font)
    
    def ok_cb(self):
        self.cancel = False
        self.paths = [
            self.listWidget.item(i).text() 
            for i in range(self.listWidget.count())
        ]
        self.close()
    
    def addFolderPath(self, path):
        self.listWidget.addItem(path)
    
    def removePaths(self):
        for item in self.listWidget.selectedItems():
            row = self.listWidget.row(item)
            self.listWidget.takeItem(row)

class SetMeasurementsDialog(QBaseDialog):
    sigOk = Signal(object)
    
    def __init__(
            self, parent=None, selectedMeasurements=None, 
            isSpotFitRequested=False
        ):
        self.cancel = True
        
        super().__init__(parent=parent)
        
        self.setWindowTitle('Set spotMAX measurements to save')
        
        self.tabWidget = QTabWidget()
        
        self.lastSelectionCp = None
        if os.path.exists(last_selection_meas_filepath):
            self.lastSelectionCp = config.ConfigParser()
            self.lastSelectionCp.read(last_selection_meas_filepath)
        
        mainLayout = QVBoxLayout()
        
        searchLineEdit = acdc_widgets.SearchLineEdit()
        
        showColNamesLabel = QLabel('Show column names')
        colNamesToggle = acdc_widgets.Toggle()
        
        searchLayout = QHBoxLayout()
        searchLayout.addWidget(showColNamesLabel)
        searchLayout.addWidget(colNamesToggle)
        searchLayout.addStretch(2)
        searchLayout.addWidget(searchLineEdit)
        searchLayout.setStretch(3, 1)
        
        self.groupBoxes = {'single_spot': {}, 'aggr': {}}
        
        self.singleSpotFeatGroups = features.get_features_groups()
        self.singleSpotFeatToColMapper = (
            features.feature_names_to_col_names_mapper()
        )
        singleSpotTab = self.buildTab(
            isSpotFitRequested, self.singleSpotFeatGroups, 'single_spot'
        )        
        self.tabWidget.addTab(singleSpotTab, 'Single-spot measurements')
        
        self.aggrFeatGroups = features.get_aggr_features_groups()
        self.aggrFeatToColMapper = (
            features.aggr_feature_names_to_col_names_mapper()
        )
        aggrTab = self.buildTab(
            isSpotFitRequested, self.aggrFeatGroups, 'aggr'
        )        
        self.tabWidget.addTab(aggrTab, 'Aggregated measurements')
        
        self.mappers = {
            'single_spot': self.singleSpotFeatToColMapper,
            'aggr': self.aggrFeatToColMapper
        }
        self.groups = {
            'single_spot': self.singleSpotFeatGroups,
            'aggr': self.aggrFeatGroups
        }
        
        self.setSelectedMeasurementsChecked(selectedMeasurements)
        
        additionalButtons = []
        self.selectAllButton = acdc_widgets.selectAllPushButton()
        self.selectAllButton.sigClicked.connect(self.setCheckedAll)
        additionalButtons.append(self.selectAllButton)
        
        if self.lastSelectionCp is not None:
            self.loadLastSelButton = acdc_widgets.reloadPushButton(
                '  Load last selection...  '
            )
            self.loadLastSelButton.clicked.connect(self.loadLastSelection)
            additionalButtons.append(self.loadLastSelButton)
            
        buttonsLayout = acdc_widgets.CancelOkButtonsLayout(
            additionalButtons=additionalButtons
        )
            
        buttonsLayout.okButton.clicked.connect(self.ok_cb)
        buttonsLayout.cancelButton.clicked.connect(self.close)
        
        mainLayout.addLayout(searchLayout)
        mainLayout.addSpacing(20)
        mainLayout.addWidget(self.tabWidget)
        mainLayout.addSpacing(20)
        mainLayout.addLayout(buttonsLayout)
        
        self.setFont(font)
        self.setLayout(mainLayout)       
        
        searchLineEdit.textEdited.connect(self.searchAndHighlight)
        colNamesToggle.toggled.connect(self.showColNamesToggled)
    
    def buildTab(self, isSpotFitRequested, featGroups, tabKey):
        maxNumElementsPerVBox = 15
        rowNumElements = 0
        row = 0
        groupBoxesHLayout = QHBoxLayout()
        groupBoxesVLayout = QVBoxLayout()
        for groupName, metrics in featGroups.items():
            rowSpan = len(metrics) + 1
            rowNumElements += rowSpan
            if rowNumElements >= maxNumElementsPerVBox:
                groupBoxesHLayout.addLayout(groupBoxesVLayout) 
                groupBoxesVLayout = QVBoxLayout()
                rowNumElements = 0
                row = 0
            
            if tabKey == 'single_spot':
                infoUrl = docs.single_spot_feature_group_name_to_url(groupName)
            else:
                infoUrl = docs.aggr_feature_group_name_to_url(groupName)
                
            itemsInfoUrls = {name:infoUrl for name in metrics}
            
            lastSelection = self.getLastSelectionSection(
                self.lastSelectionCp, f'{tabKey};;{groupName}'
            )      
            
            groupbox = acdc_widgets.SetMeasurementsGroupBox(
                groupName, metrics, parent=self, lastSelection=lastSelection,
                itemsInfoUrls=itemsInfoUrls
            )
            groupBoxesVLayout.addWidget(groupbox)
            groupBoxesVLayout.setStretch(row, rowSpan)
            row += 1
            # printl(groupName, row, col, rowSpan)
            # groupBoxesLayout.addWidget(groupbox, row, col, rowSpan, 1)           
            self.groupBoxes[tabKey][groupName] = groupbox
            
            if not isSpotFitRequested and groupName.startswith('Spotfit'):
                groupbox.setChecked(False)
                groupbox.setDisabled(True)
                groupbox.setToolTip(
                    'Spotfit metrics cannot be saved because you did not '
                    'activate the parameter "Compute spots size".'
                )
        
        # Add last layout
        groupBoxesHLayout.addLayout(groupBoxesVLayout)
        
        widget = QWidget()
        widget.setLayout(groupBoxesHLayout)
        return widget
    
    def setSelectedMeasurementsChecked(self, selectedMeasurements):
        if selectedMeasurements is None:
            return
        for tabKey, groupboxes in self.groupBoxes.items():
            if tabKey not in selectedMeasurements:
                continue
            
            mapper = self.mappers[tabKey]
            for groupName, groupbox in groupboxes.items():
                for checkbox in groupbox.checkboxes.values():
                    key = f'{groupName}, {checkbox.text()}'
                    colname = mapper[key]
                    checkbox.setChecked(
                        colname in selectedMeasurements[tabKey]
                    )
    
    def getLastSelectionSection(self, lastSelectionCp, sectionName):
        if lastSelectionCp is None:
            return
        
        if not lastSelectionCp.has_section(sectionName):
            return
        
        lastSelection = {}
        for option in lastSelectionCp.options(sectionName):
            lastSelection[option] = lastSelectionCp.getboolean(
                sectionName, option
            )
        
        return lastSelection
    
    def searchAndHighlight(self, text):
        if len(text) == 1:
            return
        
        for tabKey, groupboxes in self.groupBoxes.items():
            for groupName, groupbox in groupboxes.items():
                groupbox.highlightCheckboxesFromSearchText(text)
    
    def setCheckedAll(self, checked):
        for tabKey, groupboxes in self.groupBoxes.items():
            for groupName, groupbox in groupboxes.items():
                groupbox.selectAllButton.setChecked(checked)
    
    def loadLastSelection(self):
        for tabKey, groupboxes in self.groupBoxes.items():
            for groupName, groupbox in groupboxes.items():
                if not hasattr(groupbox, 'loadLastSelButton'):
                    continue
                groupbox.loadLastSelButton.click()
    
    def showColNamesToggled(self, checked):
        for tabKey, groupboxes in self.groupBoxes.items():
            mapper = self.mappers[tabKey]
            groups = self.groups[tabKey]
            for groupName, groupbox in groupboxes.items():
                for c, checkbox in enumerate(groupbox.checkboxes.values()):
                    if checked:
                        key = f'{groupName}, {checkbox.text()}'
                        colname = mapper[key]
                        newText = colname
                    else:
                        newText = groups[groupName][c]
                    checkbox.setText(newText)
        QTimer.singleShot(200, self.resizeGroupBoxes)
    
    def resizeGroupBoxes(self):
        for tabKey, groupboxes in self.groupBoxes.items():
            for groupName, groupbox in groupboxes.items():
                groupbox.resizeWidthNoScrollBarNeeded()
    
    def saveLastSelection(self):
        cp = config.ConfigParser()
        for tabKey, groupboxes in self.groupBoxes.items():
            for groupName, groupbox in groupboxes.items():
                if not groupbox.isChecked():
                    continue
                cp[f'{tabKey};;{groupName}'] = {}
                for name, checkbox in groupbox.checkboxes.items():
                    cp[f'{tabKey};;{groupName}'][name] = str(checkbox.isChecked())
        with open(last_selection_meas_filepath, 'w') as ini:
            cp.write(ini)
    
    def getSelectedMeasurements(self):
        selectedMeasurements = {}
        for tabKey, groupboxes in self.groupBoxes.items():
            selectedMeasurements[tabKey] = {}
            mapper = self.mappers[tabKey]
            for groupName, groupbox in groupboxes.items():
                if not groupbox.isChecked():
                    continue
                for c, checkbox in enumerate(groupbox.checkboxes.values()):
                    if not checkbox.isChecked():
                        continue
                    key = f'{groupName}, {checkbox.text()}'
                    colname = mapper[key]
                    selectedMeasurements[tabKey][colname] = key
        return selectedMeasurements
                
    def ok_cb(self):
        self.cancel = False
        self.saveLastSelection()
        selectedMeasurements = self.getSelectedMeasurements()
        self.close()
        self.sigOk.emit(selectedMeasurements)
    
    def show(self, block=False):
        super().show(block=False)
        topScreen = self.screen().geometry().top()
        leftScreen = self.screen().geometry().left()
        screenHeight = self.screen().size().height()
        screenWidth = self.screen().size().width()
        topWindow = round(topScreen + (0.15*screenHeight/2))
        leftWindow = round(leftScreen + (0.3*screenWidth/2))
        widthWindow = round(0.7*screenWidth)
        heightWindow = round(0.85*screenHeight)
        self.setGeometry(leftWindow, topWindow, widthWindow, heightWindow)
        QTimer.singleShot(200, self.resizeGroupBoxes)
        super().show(block=block)
        
