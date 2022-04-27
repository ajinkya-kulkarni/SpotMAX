import sys
import os
import time
from functools import wraps

import numpy as np
import pandas as pd
import h5py

import skimage.io

from PyQt5.QtCore import pyqtSignal, QObject, QRunnable

from . import io, utils

"""
QRunnables or QObjects that run in QThreadPool or QThread in a PyQT app
example of usage:

    self.progressWin = dialogs.QDialogWorkerProcess(
        title='Loading data...', parent=self,
        pbarDesc=f'Loading "{channelDataPath}"...'
    )
    self.progressWin.show(self.app)
    self.startLoadDataWorker()

def startLoadDataWorker(self):
    self.funcDescription = 'loading data'
    worker = qtworkers.loadDataWorker(self)
    worker.signals.finished.connect(self.loadDataWorkerFinished)
    worker.signals.progress.connect(self.workerProgress)
    worker.signals.initProgressBar.connect(self.workerInitProgressbar)
    worker.signals.progressBar.connect(self.workerUpdateProgressbar)
    worker.signals.critical.connect(self.workerCritical)
    self.threadPool.start(worker)

def loadDataWorkerFinished(self):
    self.progressWin.workerFinished = True
    self.progressWin.close()
    ... more code
"""

def worker_exception_handler(func):
    @wraps(func)
    def run(self):
        try:
            result = func(self)
        except Exception as error:
            result = None
            self.signals.critical.emit(error)
        return result
    return run

class signals(QObject):
    finished = pyqtSignal(object)
    finishedNextStep = pyqtSignal(object, str, str)
    progress = pyqtSignal(str, object)
    sigLoadedData = pyqtSignal(object, object, str, str)
    initProgressBar = pyqtSignal(int)
    progressBar = pyqtSignal(int)
    critical = pyqtSignal(object)
    sigLoadingNewChunk = pyqtSignal(object)

class pathScannerWorker(QRunnable):
    def __init__(self, selectedPath):
        QRunnable.__init__(self)
        self.signals = signals()
        self.selectedPath = selectedPath

    @worker_exception_handler
    def run(self):
        selectedPath = self.selectedPath
        areDirsPosFolders = [
            f.find('Position_')!=-1 and os.path.isdir(os.path.join(selectedPath, f))
            for f in utils.listdir(selectedPath)
        ]
        is_selectedPath = any(areDirsPosFolders)

        pathScanner = io.expFolderScanner(selectedPath)
        if is_selectedPath:
            pathScanner.expPaths = [selectedPath]
        else:
            pathScanner.getExpPaths(
                pathScanner.homePath, signals=self.signals
            )
            numExps = len(pathScanner.expPaths)
            self.signals.progress.emit(
                f'Number of valid experiments found = {numExps}',
                'INFO'
            )

        self.signals.initProgressBar.emit(len(pathScanner.expPaths))
        pathScanner.infoExpPaths(pathScanner.expPaths, signals=self.signals)
        self.signals.finished.emit(pathScanner)

class loadDataWorker(QRunnable):
    def __init__(self, mainWin, selectedPos, selectedExpName):
        QRunnable.__init__(self)
        self.signals = signals()
        self.selectedPos = selectedPos
        self.selectedExpName = selectedExpName
        self.mainWin = mainWin

    @worker_exception_handler
    def run(self):
        expInfo = self.mainWin.expPaths[self.selectedExpName]

        posDataRef = self.mainWin.posDataRef
        channelDataPaths = expInfo['channelDataPaths'][:posDataRef.loadSizeS]

        user_ch_name = self.mainWin.user_ch_name
        logger = self.mainWin.logger
        dataSide = self.mainWin.expData[self.mainWin.lastLoadedSide]
        self.signals.initProgressBar.emit(len(channelDataPaths))
        for channelDataPath in channelDataPaths:
            posFoldername = channelDataPath.parents[1].name
            skipPos = (
                self.selectedPos is not None
                and not posFoldername == self.selectedPos
            )
            if skipPos:
                # To avoid memory issues we load single pos for time-lapse and
                # all pos for static data
                continue

            posData = io.loadData(channelDataPath, user_ch_name)
            self.signals.progress.emit(
                f'Loading {posData.relPath}...',
                'INFO'
            )
            posData.loadSizeS = posDataRef.loadSizeS
            posData.loadSizeT = posDataRef.loadSizeT
            posData.loadSizeZ = posDataRef.loadSizeZ
            posData.SizeT = posDataRef.SizeT
            posData.SizeZ = posDataRef.SizeZ
            posData.getBasenameAndChNames(load=False)
            posData.buildPaths()
            posData.loadChannelData()
            posData.loadOtherFiles(
                load_segm_data=True,
                load_acdc_df=True,
                loadSegmInfo=True,
                load_last_tracked_i=True,
                load_metadata=True,
                load_ref_ch_mask=True,
            )
            if posDataRef.SizeZ > 1:
                SizeZ = posData.chData_shape[-3]
                posData.SizeZ = SizeZ
            else:
                posData.SizeZ = 1

            posData.TimeIncrement = posDataRef.TimeIncrement
            posData.PhysicalSizeZ = posDataRef.PhysicalSizeZ
            posData.PhysicalSizeY = posDataRef.PhysicalSizeY
            posData.PhysicalSizeX = posDataRef.PhysicalSizeX
            posData.saveMetadata()

            posData.computeSegmRegionprops()

            logger.info(f'Channel data shape = {posData.chData_shape}')
            logger.info(f'Loaded data shape = {posData.chData.shape}')
            logger.info(f'Metadata:')
            logger.info(posData.metadata_df)

            dataSide.append(posData)

            self.signals.progressBar.emit(1)

        self.signals.finished.emit(None)

class loadChunkDataWorker(QObject):
    sigLoadingFinished = pyqtSignal(object)

    def __init__(self, mutex, waitCond, readH5mutex, waitReadH5cond):
        QObject.__init__(self)
        self.signals = signals()
        self.mutex = mutex
        self.waitCond = waitCond
        self.exit = False
        self.sender = None
        self.H5readWait = False
        self.waitReadH5cond = waitReadH5cond
        self.readH5mutex = readH5mutex

    def setArgs(self, posData, side, current_idx, axis, updateImgOnFinished):
        self.wait = False
        self.updateImgOnFinished = updateImgOnFinished
        self.side = side
        self.posData = posData
        self.current_idx = current_idx
        self.axis = axis

    def pauseH5read(self):
        self.readH5mutex.lock()
        self.waitReadH5cond.wait(self.mutex)
        self.readH5mutex.unlock()

    def pause(self):
        self.mutex.lock()
        self.waitCond.wait(self.mutex)
        self.mutex.unlock()

    @worker_exception_handler
    def run(self):
        while True:
            if self.exit:
                self.signals.progress.emit(
                    'Closing load chunk data worker...', 'INFO'
                )
                break
            elif self.wait:
                self.signals.progress.emit(
                    'Load chunk data worker paused.', 'INFO'
                )
                self.pause()
            else:
                self.signals.progress.emit(
                    'Load chunk data worker resumed.', 'INFO'
                )
                self.posData.loadChannelDataChunk(
                    self.current_idx, axis=self.axis, worker=self
                )
                self.sigLoadingFinished.emit(self.side)
                self.wait = True

        self.signals.finished.emit(None)

class load_H5Store_Worker(QRunnable):
    def __init__(self, expData, h5_filename, side):
        QRunnable.__init__(self)
        self.signals = signals()
        self.expData = expData
        self.h5_filename = h5_filename
        self.side = side

    @worker_exception_handler
    def run(self):
        for posData in self.expData[self.side]:
            h5_path = os.path.join(
                posData.spotmaxOutPath, self.h5_filename
            )
            if not os.path.exists(h5_path):
                posData.hdf_store = None
                self.progress.emit(
                    f'WARNING: {self.h5_filename} not found '
                    f'in {posData.spotmaxOutPath}',
                    'WARNING'
                )
                continue

            posData.h5_path = h5_path
            posData.hdf_store = pd.HDFStore(posData.h5_path, mode='r')
            self.signals.progressBar.emit(1)
        self.signals.finished.emit(self.side)

class load_relFilenameData_Worker(QRunnable):
    """
    Load data given a list of relative filenames
    (filename without the common basename)
    """
    def __init__(self, expData, relFilenames, side, nextStep):
        QRunnable.__init__(self)
        self.signals = signals()
        self.expData = expData
        self.relFilenames = relFilenames
        self.side = side
        self.nextStep = nextStep

    @worker_exception_handler
    def run(self):
        for posData in self.expData[self.side]:
            for relFilename in self.relFilenames:
                if relFilename in posData.loadedRelativeFilenamesData:
                    continue
                filepath = posData.absoluteFilepath(relFilename)
                filename = os.path.basename(filepath)
                self.signals.progress.emit(f'Loading {filepath}...', 'INFO')
                ext = os.path.splitext(filename)[1]
                if ext == '.tif':
                    data = skimage.io.imread(filepath)
                elif ext == '.npy':
                    data = np.load(filepath)
                elif ext == '.npz':
                    data = np.load(filepath)['arr_0']
                elif ext == '.h5':
                    h5f = h5py.File(filepath, 'r')
                    data = h5f['data']
                self.signals.sigLoadedData.emit(
                    posData, data, relFilename, self.nextStep
                )
            self.signals.progressBar.emit(1)
        self.signals.finishedNextStep.emit(
            self.side, self.nextStep, self.relFilenames[0]
        )

class skeletonizeWorker(QRunnable):
    def __init__(self, expData, side, initFilename=False):
        QRunnable.__init__(self)
        self.signals = signals()
        self.expData = expData
        self.side = side
        self.initFilename = initFilename

    @worker_exception_handler
    def run(self):
        for posData in self.expData[self.side]:
            if self.initFilename:
                relFilename = list(posData.loadedRelativeFilenamesData)[0]
                posData.skeletonizedRelativeFilename = relFilename
            relFilename = posData.skeletonizedRelativeFilename
            filepath = posData.absoluteFilepath(relFilename)
            filename = os.path.basename(filepath)
            dataToSkel = posData.loadedRelativeFilenamesData[relFilename]

            self.signals.progress.emit(f'Skeletonizing {filepath}...', 'INFO')
            posData.skeletonize(dataToSkel)

            self.signals.progressBar.emit(1)
        self.signals.finished.emit(self.side)


class findContoursWorker(QRunnable):
    def __init__(self, expData, side, initFilename=False):
        QRunnable.__init__(self)
        self.signals = signals()
        self.expData = expData
        self.side = side
        self.initFilename = initFilename

    @worker_exception_handler
    def run(self):
        for posData in self.expData[self.side]:
            if self.initFilename:
                relFilename = list(posData.loadedRelativeFilenamesData)[0]
                posData.contouredRelativeFilename = relFilename
            relFilename = posData.contouredRelativeFilename
            filepath = posData.absoluteFilepath(relFilename)
            filename = os.path.basename(filepath)
            dataToCont = posData.loadedRelativeFilenamesData[relFilename]

            self.signals.progress.emit(
                f'Computing contour of {filepath}...', 'INFO'
            )
            posData.contours(dataToCont)

            self.signals.progressBar.emit(1)
        self.signals.finished.emit(self.side)
