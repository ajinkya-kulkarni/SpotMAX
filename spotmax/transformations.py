import os

import math
import numpy as np
import pandas as pd

import scipy.ndimage
import skimage.measure

import cellacdc.io
import cellacdc.core

from . import utils, rng
from . import ZYX_RESOL_COLS, ZYX_LOCAL_COLS
from . import features
from . import io

from . import printl, error_up_str

def get_slices_local_into_global_3D_arr(zyx_center, global_shape, local_shape):
    """Generate the slices required to insert a local mask into a larger image.

    Parameters
    ----------
    zyx_center : (3,) ArrayLike
        Array, tuple, or list of `z, y, x` center coordinates
    global_shape : tuple
        Shape of the image where the mask will be inserted.
    local_shape : tuple
        Shape of the mask to be inserted into the image.

    Returns
    -------
    tuple
        - `slice_global_to_local`: used to slice the image to the same shape of 
        the cropped mask.
        - `slice_crop_local`: used to crop the local mask before inserting it 
        into the image.
    """    
    if len(global_shape) == 2:
        global_shape = (1, *global_shape)
    
    if len(local_shape) == 2:
        local_shape = (1, *local_shape)
        
    slice_global_to_local = []
    slice_crop_local = []
    for _c, _d, _D in zip(zyx_center, local_shape, global_shape):
        _r = int(_d/2)
        _min = _c - _r
        _max = _min + _d
        _min_crop, _max_crop = None, None
        if _min < 0:
            _min_crop = abs(_min)
            _min = 0
        if _max > _D:
            _max_crop = _D - _max
            _max = _D
        
        slice_global_to_local.append(slice(_min, _max))
        slice_crop_local.append(slice(_min_crop, _max_crop))
    
    return tuple(slice_global_to_local), tuple(slice_crop_local)

def get_expanded_obj_slice_image(obj, delta_expand, lab):
    Z, Y, X = lab.shape
    crop_obj_start = np.array([s.start for s in obj.slice]) - delta_expand
    crop_obj_start = np.clip(crop_obj_start, 0, None)

    crop_obj_stop = np.array([s.stop for s in obj.slice]) + delta_expand
    crop_obj_stop = np.clip(crop_obj_stop, None, (Z, Y, X))
    
    obj_slice = (
        slice(crop_obj_start[0], crop_obj_stop[0]), 
        slice(crop_obj_start[1], crop_obj_stop[1]),  
        slice(crop_obj_start[2], crop_obj_stop[2]), 
    )
    obj_lab = lab[obj_slice]
    obj_image = obj_lab==obj.label
    return obj_slice, obj_image, crop_obj_start

def get_expanded_obj(obj, delta_expand, lab):
    expanded_obj = utils._Dummy(name='ExpandedObject')
    expanded_results = get_expanded_obj_slice_image(
        obj, delta_expand, lab
    )
    obj_slice, obj_image, crop_obj_start = expanded_results
    expanded_obj.slice = obj_slice
    expanded_obj.label = obj.label
    expanded_obj.crop_obj_start = crop_obj_start
    expanded_obj.image = obj_image
    return expanded_obj

def expand_labels(label_image, distance=1, zyx_vox_size=None):
    distances, nearest_label_coords = scipy.ndimage.distance_transform_edt(
        label_image==0, return_indices=True, sampling=zyx_vox_size,
    )
    labels_out = np.zeros_like(label_image)
    dilate_mask = distances <= distance
    # build the coordinates to find nearest labels
    masked_nearest_label_coords = [
        dimension_indices[dilate_mask]
        for dimension_indices in nearest_label_coords
    ]
    nearest_labels = label_image[tuple(masked_nearest_label_coords)]
    labels_out[dilate_mask] = nearest_labels
    return labels_out

def get_aggregate_obj_slice(
        obj, max_h_top, max_height, max_h_bottom, max_d_fwd, max_depth, 
        max_d_back, img_data_shape, dx=0
    ):
    Z, Y, X = img_data_shape
    slice_w = obj.slice[2]
    x_left, x_right = slice_w.start-int(dx/2), slice_w.stop+int(dx/2)
    if x_left < 0:
        x_left = 0
    if x_right > X:
        x_right = X

    slice_w = slice(x_left, x_right)
    zmin, ymin, xmin, zmax, ymax, xmax = obj.bbox
    z, y = int(zmin+(zmax-zmin)/2), int(ymin+(ymax-ymin)/2)
    h_top = y - max_h_top
    if h_top < 0:
        # Object slicing would extend negative y
        h_top = 0
        h_bottom = max_height
    else:
        h_bottom = y + max_h_bottom
    
    if h_bottom > Y:
        # Object slicing would extend more than the img data Y
        h_bottom = Y
        h_top = h_bottom - max_height
    
    d_fwd = z - max_d_fwd
    if d_fwd < 0:
        # Object slicing would extend negative z
        d_fwd = 0
        d_top = max_depth
    else:
        # Object slicing would extend more than the img data Z
        d_top = z + max_d_back
    
    if d_top > Z:
        d_top = Z
        d_fwd = d_top - max_depth

    obj_slice = (
        slice(d_fwd, d_top), slice(h_top, h_bottom), slice_w
    )
    return obj_slice

def _aggregate_objs(
        img_data, lab, zyx_tolerance=None, debug=False, 
        return_x_slice_idxs=False
    ):
    # Add tolerance based on resolution limit
    if zyx_tolerance is not None:
        dz, dy, dx = zyx_tolerance
    else:
        dz, dy, dx = 0, 0, 0

    # Get max height and total width
    rp_merged = skimage.measure.regionprops(lab)
    tot_width = 0
    max_height = 0
    max_depth = 0
    for obj in rp_merged:
        d, h, w = obj.image.shape
        d, h, w = d+dz, h+dy, w+dx
        if h > max_height:
            max_height = h
        if d > max_depth:
            max_depth = d
        tot_width += w

    Z, Y, X = lab.shape
    if max_depth > Z:
        max_depth = Z
    if max_height > Y:
        max_height = Y
    
    if return_x_slice_idxs:
        x_slice_idxs = []
    
    # Aggregate data horizontally by slicing object centered at 
    # centroid and using largest object as slicing box
    aggr_shape = (max_depth, max_height, tot_width)
    max_h_top = int(max_height/2)
    max_h_bottom = max_height-max_h_top
    max_d_fwd = int(max_depth/2)
    max_d_back = max_depth-max_d_fwd
    aggregated_img = np.zeros(aggr_shape, dtype=img_data.dtype)
    aggregated_img[:] = aggregated_img.min()
    aggregated_lab = np.zeros(aggr_shape, dtype=lab.dtype)
    last_w = 0
    excess_width = 0
    for obj in rp_merged:
        w = obj.image.shape[-1] + dx
        obj_slice = get_aggregate_obj_slice(
            obj, max_h_top, max_height, max_h_bottom, max_d_fwd, max_depth, 
            max_d_back, img_data.shape, dx=dx
        )
        obj_width = obj_slice[-1].stop - obj_slice[-1].start
        excess_width += w - obj_width
        slice_x_end = last_w+obj_width
        aggregated_img[:, :, last_w:slice_x_end] = img_data[obj_slice]
        obj_lab = lab[obj_slice].copy()
        obj_lab[obj_lab != obj.label] = 0
        aggregated_lab[:, :, last_w:slice_x_end] = obj_lab
        last_w += obj_width
        if return_x_slice_idxs:
            x_slice_idxs.append(slice_x_end)
    if excess_width > 0:
        # Trim excess width result of adding dx to all objects
        aggregated_img = aggregated_img[..., :-excess_width]
        aggregated_lab = aggregated_lab[..., :-excess_width]
    
    if return_x_slice_idxs:
        return aggregated_img, aggregated_lab, x_slice_idxs
    else:
        return aggregated_img, aggregated_lab

def _merge_moth_bud(lineage_table, lab, return_bud_images=False):
    if lineage_table is None:
        if return_bud_images:
            return lab, {}
        else:
            return lab
    
    df_buds = lineage_table[lineage_table.relationship == 'bud']
    moth_IDs = df_buds['relative_ID'].unique()
    df_buds = df_buds.reset_index().set_index('relative_ID')
    if len(moth_IDs) == 0:
        if return_bud_images:
            return lab, {}
        else:
            return lab
    
    lab_merged = lab.copy()
    if return_bud_images:
        bud_images = {}
    for mothID in moth_IDs:
        budID = df_buds.at[mothID, 'Cell_ID']
        lab_merged[lab==budID] = mothID
        if return_bud_images:
            moth_bud_image = np.zeros(lab_merged.shape, dtype=np.uint8)
            moth_bud_image[lab==budID] = 1
            moth_bud_image[lab==mothID] = 1
            moth_bud_obj = skimage.measure.regionprops(moth_bud_image)[0]
            moth_bud_image[lab==mothID] = 0
            bud_image = moth_bud_image[moth_bud_obj.slice] > 0
            bud_images[mothID] = {
                'image': bud_image, 'budID': budID
            }
    if return_bud_images:
        return lab_merged, bud_images
    else:
        return lab_merged

def _separate_moth_buds(lab_merged, bud_images):
    rp = skimage.measure.regionprops(lab_merged)
    for obj in rp:
        if obj.label not in bud_images:
            continue
        bud_info = bud_images.get(obj.label)
        budID = bud_info['budID']
        bud_image = bud_info['image']
        lab_merged[obj.slice][bud_image] = budID
    return lab_merged

def aggregate_objs(
        img_data, lab, zyx_tolerance=None, return_bud_images=True, 
        additional_imgs_to_aggr=None, lineage_table=None, debug=False, 
        return_x_slice_idxs=False
    ):
    lab_merged, bud_images = _merge_moth_bud(
        lineage_table, lab, return_bud_images=return_bud_images
    )
        
    aggregated_img, aggregated_lab, x_slice_idxs = _aggregate_objs(
        img_data, lab_merged, zyx_tolerance=zyx_tolerance, debug=debug, 
        return_x_slice_idxs=True
    )
    if additional_imgs_to_aggr is not None:
        additional_aggr_imgs = []
        for _img in additional_imgs_to_aggr:
            if _img is None:
                additional_aggr_imgs.append(None)
                continue
            additional_aggr_img, _ = _aggregate_objs(
                _img, lab_merged, zyx_tolerance=zyx_tolerance, debug=debug
            )
            additional_aggr_imgs.append(additional_aggr_img)
    else:
        additional_aggr_imgs = [None]
    
    # if debug:
    #     from cellacdc.plot import imshow
    #     imshow(aggregated_img, aggregated_lab)
    #     import pdb; pdb.set_trace()
    
    aggregated_lab = _separate_moth_buds(
        aggregated_lab, bud_images
    )
    if return_x_slice_idxs:
        return aggregated_img, aggregated_lab, additional_aggr_imgs, x_slice_idxs
    else:
        return aggregated_img, aggregated_lab, additional_aggr_imgs

class SliceImageFromSegmObject:
    def __init__(self, lab, lineage_table=None):
        self._lab = lab
        self._lineage_df = lineage_table
    
    def _get_obj_mask(self, obj):
        lab_obj_image = self._lab == obj.label
        
        if self._lineage_df is None:
            return lab_obj_image, -1
        
        cc_stage = self._lineage_df.at[obj.label, 'cell_cycle_stage']
        if cc_stage == 'G1':
            return lab_obj_image, -1
        
        # Merge mother and daughter when in S phase
        rel_ID = self._lineage_df.at[obj.label, 'relative_ID']
        lab_obj_image = np.logical_or(
            self._lab == obj.label, self._lab == rel_ID
        )
        
        return lab_obj_image, rel_ID
    
    def _get_obj_lab(self, lab_mask):
        lab_mask_lab = np.zeros_like(self._lab)
        lab_mask_lab[lab_mask] = self._lab[lab_mask]
        return lab_mask_lab
    
    def slice(self, image, obj):
        lab_mask, bud_ID = self._get_obj_mask(obj)
        lab_mask_lab = self._get_obj_lab(lab_mask)
        lab_mask_rp = skimage.measure.regionprops(lab_mask.astype(np.uint8))
        lab_mask_obj = lab_mask_rp[0]
        img_local = image[lab_mask_obj.slice].copy()
        backgr_vals = img_local[~lab_mask_obj.image]
        # if backgr_vals.size == 0:
        #     return img_local, lab_mask_lab, lab_mask_obj.image, bud_ID
        
        # backgr_mean = backgr_vals.mean()
        # backgr_mean = backgr_mean if backgr_mean>=0 else 0
        # backgr_std = backgr_vals.std()/2
        # gamma_shape = np.square(backgr_mean/backgr_std)
        # gamma_scale = np.square(backgr_std)/backgr_mean
        # img_backgr = rng.gamma(
        #     gamma_shape, gamma_scale, size=lab_mask_obj.image.shape
        # )
        # img_backgr = rng.normal(
        #     backgr_mean, backgr_std, size=lab_mask_obj.image.shape
        # )
        # np.clip(img_backgr, 0, 1, out=img_backgr)

        # img_backgr[lab_mask_obj.image] = img_local[lab_mask_obj.image]
    
        # # Replace values outside of the obj mask that are higher than the 
        # # max with mean of the object to avoid external objects with 
        # # bright features to skew thresholding the object
        # foregr_vals = img_local[lab_mask_obj.image]
        # foregr_mean = foregr_vals.mean()
        # foregr_max = foregr_vals.max()
        # img_local[img_local > foregr_max] = foregr_mean
        
        return img_local, lab_mask_lab, lab_mask_obj.slice, bud_ID

def crop_from_segm_data_info(segm_data, delta_tolerance, lineage_table=None):
    if segm_data.ndim != 4:
        ndim = segm_data.ndim
        raise TypeError(
            f'Input segmentation data has {ndim} dimensions. Only 4D data allowed. '
            'Make sure to reshape your input data to shape `(Time, Z-slices, Y, X)`.'
        )
    
    if not np.any(segm_data):
        segm_slice = (slice(None), slice(None), slice(None), slice(None))
        crop_to_global_coords = np.array([0, 0, 0])
        pad_widths = [(0, 0), (0, 0), (0, 0), (0, 0)]
        return segm_slice, pad_widths, crop_to_global_coords
        
    T, Z, Y, X = segm_data.shape
    if lineage_table is not None:
        frames_ccs_values = lineage_table[['cell_cycle_stage']].dropna()
        stop_frame_i = frames_ccs_values.index.get_level_values(0).max()
        stop_frame_num = stop_frame_i + 1
    else:
        stop_frame_num = T
    
    segm_data = segm_data[:stop_frame_num]
    
    segm_time_proj = np.any(segm_data, axis=0).astype(np.uint8)
    segm_time_proj_obj = skimage.measure.regionprops(segm_time_proj)[0]

    # Store cropping coordinates to save correct spots coordinates
    crop_to_global_coords = np.array([
        s.start for s in segm_time_proj_obj.slice
    ]) 
    crop_to_global_coords = crop_to_global_coords - delta_tolerance
    crop_to_global_coords = np.clip(crop_to_global_coords, 0, None)

    crop_stop_coords = np.array([
        s.stop for s in segm_time_proj_obj.slice
    ]) 
    crop_stop_coords = crop_stop_coords + delta_tolerance
    crop_stop_coords = np.clip(crop_stop_coords, None, (Z, Y, X))

    # Build (z,y,x) cropping slices
    z_start, y_start, x_start = crop_to_global_coords        
    z_stop, y_stop, x_stop = crop_stop_coords  
    segm_slice = (
        slice(0, stop_frame_num), slice(z_start, z_stop), 
        slice(y_start, y_stop), slice(x_start, x_stop)
    )

    pad_widths = []
    for _slice, D in zip(segm_slice, (stop_frame_num, Z, Y, X)):
        _pad_width = [0, 0]
        if _slice.start is not None:
            _pad_width[0] = _slice.start
        if _slice.stop is not None:
            _pad_width[1] = D - _slice.stop
        pad_widths.append(tuple(_pad_width))

    return segm_slice, pad_widths, crop_to_global_coords

def deaggregate_img(aggr_img, aggregated_lab, lab):
    deaggr_img = np.zeros(lab.shape, dtype=aggr_img.dtype)
    rp = skimage.measure.regionprops(lab)
    aggr_rp = skimage.measure.regionprops(aggregated_lab)
    aggr_rp = {aggr_obj.label:aggr_obj for aggr_obj in aggr_rp}
    for obj in rp:
        aggr_obj = aggr_rp[obj.label]
        deaggr_img[obj.slice] = aggr_img[aggr_obj.slice]
    return deaggr_img

def index_aggregated_segm_into_input_lab(
        lab, aggregated_segm, aggregated_lab, x_slice_idxs,
        keep_objects_touching_lab_intact=False
    ):    
    with open('x_slice_idxs.txt', 'w') as txt:
        txt.write(str(x_slice_idxs))
        
    subobj_labels = np.zeros_like(lab)
    rp = skimage.measure.regionprops(lab)
    obj_idxs = {obj.label:obj for obj in rp}
    aggr_rp = skimage.measure.regionprops(aggregated_lab)
    aggr_obj_idxs = {aggr_obj.label:aggr_obj for aggr_obj in aggr_rp}
    if not keep_objects_touching_lab_intact:
        aggregated_segm[aggregated_lab == 0] = False
    
    aggr_subobj_lab = np.zeros(aggregated_segm.shape, dtype=np.uint32)
    
    start_x_slice = 0
    last_max_id = 0
    for end_x_slice in x_slice_idxs:
        sliced_subobj_mask = aggregated_segm[..., start_x_slice:end_x_slice] > 0
        sliced_subobj_lab = skimage.measure.label(sliced_subobj_mask)
        sliced_subobj_lab[sliced_subobj_mask] = (
            sliced_subobj_lab[sliced_subobj_mask] + last_max_id
        )
        aggr_subobj_lab[..., start_x_slice:end_x_slice] = sliced_subobj_lab
        last_max_id = sliced_subobj_lab.max()
        start_x_slice = end_x_slice
    
    aggr_subobj_rp = skimage.measure.regionprops(aggr_subobj_lab)
    for subobj in aggr_subobj_rp:
        masked = aggregated_lab[subobj.slice][subobj.image]
        unique_vals, counts = np.unique(masked, return_counts=True)
        unique_foregr_vals_mask = unique_vals>0
        unique_foregr_vals = unique_vals[unique_foregr_vals_mask]
        counts_foregr = counts[unique_foregr_vals_mask]
        if unique_foregr_vals.size == 0:
            # Sub object is not touching any obj --> do not add
            continue
        
        max_count_idx = counts_foregr.argmax()
        ID = unique_foregr_vals[max_count_idx]
        obj = obj_idxs[ID]
        aggr_obj = aggr_obj_idxs[ID]
        z0, y0, x0 = aggr_obj.bbox[:3]
        sub_obj_local_coords = subobj.coords - (z0, y0, x0)
        sub_obj_global_coords = sub_obj_local_coords + obj.bbox[:3]
        zz, yy, xx = (
            sub_obj_global_coords[:,0], 
            sub_obj_global_coords[:,1], 
            sub_obj_global_coords[:,2]
        )
        subobj_labels[zz, yy, xx] = ID
    
    return subobj_labels

def get_local_spheroid_mask(spots_zyx_radii_pxl, logger_func=print):
    zr, yr, xr = spots_zyx_radii_pxl
    wh = int(np.ceil(yr))
    d = int(np.ceil(zr))

    # Generate a sparse meshgrid to evaluate 3D spheroid mask
    z, y, x = np.ogrid[-d:d+1, -wh:wh+1, -wh:wh+1]

    # 3D spheroid equation
    if zr > 0:
        mask = (x**2 + y**2)/(yr**2) + z**2/(zr**2) <= 1
        # # Remove empty slices
        # mask = mask[np.any(mask, axis=(0,1))]
    else:
        mask = (x**2 + y**2)/(yr**2) <= 1
    
    if d == 1:
        # If depth is 1 we expect a single z-slice mask (instead of 3)
        mask = mask.max(axis=0)[np.newaxis]
    
    return mask

def get_spheroids_maks(
        zyx_coords, mask_shape, min_size_spheroid_mask=None, 
        zyx_radii_pxl=None, debug=False
    ):
    mask = np.zeros(mask_shape, dtype=bool)
    if min_size_spheroid_mask is None:
        min_size_spheroid_mask = get_local_spheroid_mask(
            zyx_radii_pxl
        )
    
    for s, zyx_center in enumerate(zyx_coords):
        if isinstance(min_size_spheroid_mask, pd.Series):
            spot_mask = min_size_spheroid_mask.iloc[s]
        else:
            spot_mask = min_size_spheroid_mask
            
        slice_global_to_local, slice_crop_local = (
            get_slices_local_into_global_3D_arr(
                zyx_center, mask_shape, spot_mask.shape
            )
        )
        local_mask = spot_mask[slice_crop_local]
        mask[slice_global_to_local][local_mask] = True
    return mask, min_size_spheroid_mask

def get_expand_obj_delta_tolerance(spots_zyx_radii):
    if spots_zyx_radii is None:
        return np.array([0, 0, 0]).astype(int)
    delta_tol = np.array(spots_zyx_radii)
    # Allow twice the airy disk radius in y,x
    delta_tol[1:] *= 2
    delta_tol = np.ceil(delta_tol).astype(int)
    return delta_tol

def reshape_lab_image_to_3D(lab, image):
    if lab is None:
        lab = np.ones(image.shape, dtype=np.uint8) 
    
    if image.ndim == 2:
        image = image[np.newaxis]
        
    if lab.ndim == 2 and image.ndim == 3:
        # Stack 2D lab into 3D z-stack
        lab = np.array([lab]*len(image))
    return lab, image

def reshape_spots_coords_to_3D(spots_coords):
    nrows, ncols = spots_coords.shape
    if ncols == 3:
        return spots_coords
    
    if ncols == 2:
        reshaped_spots_coords = np.ones(
            (nrows, 3), dtype=spots_coords.dtype
        )
        reshaped_spots_coords[:, 1:] = spots_coords
        return reshaped_spots_coords
    
    raise TypeError(
        f'`spots_coords` has {ncols} columns. Allowed values are 2 or 3.'
    )

def to_local_zyx_coords(obj, global_zyx_coords):
    depth, height, width = obj.image.shape
    zmin, ymin, xmin, _, _, _ = obj.bbox
    local_zyx_coords = global_zyx_coords - (zmin, ymin, xmin)
    local_zyx_coords = local_zyx_coords[np.all(local_zyx_coords>=0, axis=1)]
    local_zyx_coords = local_zyx_coords[local_zyx_coords[:,0] < depth]
    local_zyx_coords = local_zyx_coords[local_zyx_coords[:,1] < height]
    local_zyx_coords = local_zyx_coords[local_zyx_coords[:,2] < width]
    zz, yy, xx = (
        local_zyx_coords[:,0], 
        local_zyx_coords[:,1], 
        local_zyx_coords[:,2]
    )
    zyx_centers_mask = obj.image[zz, yy, xx]
    local_zyx_coords = local_zyx_coords[zyx_centers_mask]
    return local_zyx_coords

def init_df_features(
        df_spots_coords, obj, crop_obj_start, spots_zyx_radii, ID=None
    ):
    if ID is None:
        ID = obj.label
        
    if obj.label not in df_spots_coords.index:
        return None, []
    
    local_peaks_coords = (
        df_spots_coords.loc[[ID], ZYX_LOCAL_COLS]
    ).to_numpy()
    zyx_local_to_global = [s.start for s in obj.slice]
    global_peaks_coords = local_peaks_coords + zyx_local_to_global
    # Add correct local_peaks_coords considering the cropping tolerance 
    # `delta_tolerance`
    local_peaks_coords_expanded = global_peaks_coords - crop_obj_start 
    spots_masks = None

    num_spots_detected = len(global_peaks_coords)
    df_features = pd.DataFrame({
        'spot_id': np.arange(1, num_spots_detected+1),
        'z': global_peaks_coords[:,0],
        'y': global_peaks_coords[:,1],
        'x': global_peaks_coords[:,2],
        'z_local': local_peaks_coords[:,0],
        'y_local': local_peaks_coords[:,1],
        'x_local': local_peaks_coords[:,2],
        'z_local_expanded': local_peaks_coords_expanded[:,0],
        'y_local_expanded': local_peaks_coords_expanded[:,1],
        'x_local_expanded': local_peaks_coords_expanded[:,2],
    })
    if 'spot_mask' in df_spots_coords.columns:
        spots_masks = (
            df_spots_coords.loc[[ID], 'spot_mask']).to_list()
        df_features['spot_mask'] = spots_masks
    
    if 'closest_ID' in df_spots_coords.columns:
        closest_IDs = (
            df_spots_coords.loc[[ID], 'closest_ID']).to_list()
        df_features['closest_ID'] = closest_IDs
    
    df_features[ZYX_RESOL_COLS] = spots_zyx_radii

    return df_features, local_peaks_coords_expanded

def norm_distance_transform_edt(mask):
    edt = scipy.ndimage.distance_transform_edt(mask)
    edt = edt/edt.max()
    return edt

def normalise_spot_by_dist_transf(
        spot_slice_z, dist_transf, backgr_vals_z_spots,
        how='range'
    ):
    if how == 'range':
        norm_spot_slice_z = features.normalise_by_dist_transform_range(
            spot_slice_z, dist_transf, backgr_vals_z_spots
        )
    elif how == 'simple':
        norm_spot_slice_z = features.normalise_by_dist_transform_simple(
            spot_slice_z, dist_transf, backgr_vals_z_spots
        )
    else:
        norm_spot_slice_z = spot_slice_z
    return norm_spot_slice_z

def load_preprocess_nnet_data_across_exp(
        exp_path, pos_foldernames, spots_ch_endname, model, 
        callback_channel_not_found=None
    ):
    images = []
    for pos in pos_foldernames:
        images_path = os.path.join(exp_path, pos, 'Images')
        ch_path = cellacdc.io.get_filepath_from_channel_name(
            images_path, os.path.basename(spots_ch_endname)
        )
        if not os.path.exists(ch_path) and callback_channel_not_found is not None:
            callback_channel_not_found(spots_ch_endname, images_path)
            return
        ch_data, ch_dtype = io.load_image_data(
            ch_path, to_float=True, return_dtype=True
        )
        images.append(ch_data)
    
    transformed = model.preprocess(images)
    transformed_data_nnet = {}
    for pos, transf_data in zip(pos_foldernames, transformed):
        transformed_data_nnet[pos] = transf_data
    return transformed_data_nnet

def _raise_norm_value_zero(self):
    print('')
    self.logger.info(
        '[ERROR]: Skipping Position, see error below. '
        f'More details in the final report.{error_up_str}'
    )
    raise FloatingPointError(
        'normalising value for the reference channel is zero.'
    )

def _warn_norm_value_zero(self):
    warning_txt = (
        'normalising value for the spots channel is zero.'
    )
    print('')
    self.logger.info(f'[WARNING]: {warning_txt}{error_up_str}')
    self.log_warning_report(warning_txt)

def normalise_img(
        img: np.ndarray, norm_mask: np.ndarray, 
        method='median', raise_if_norm_zero=True
    ):
    values = img[norm_mask]
    if method == 'median':
        norm_value = np.median(values)
    else:
        norm_value = 1

    if norm_value == 0:
        if raise_if_norm_zero:
            _raise_norm_value_zero()
        else:
            _norm_value = 1E-15
            _warn_norm_value_zero()
    else:
        _norm_value = norm_value
    norm_img = img/_norm_value
    return norm_img, norm_value

def from_spots_coords_arr_to_df(spots_coords, lab):
    ndims = spots_coords.shape[-1]
    if ndims == 2:
        yy = spots_coords[:, 0]
        xx = spots_coords[:, 1]
        zz = [0]*len(xx)
    elif ndims == 3:
        zz = spots_coords[:, 0]
        yy = spots_coords[:, 1]
        xx = spots_coords[:, 2]
    else:
        raise TypeError(
            '`spots_coords` must be a 2D array with shape (N, 2) or (N, 3) '
            f'while its shape is {spots_coords.shape}'
        )        
    
    zeros = [0]*len(xx)
    df_coords = pd.DataFrame({
        'Cell_ID': lab[zz, yy, xx],
        'spot_id': range(1, len(zz)+1),
        'z': zz,
        'y': yy, 
        'x': xx,
        'z_local': zeros,
        'y_local': zeros, 
        'x_local': zeros
    }).set_index(['Cell_ID', 'spot_id']).sort_index()
    
    for obj in skimage.measure.regionprops(lab):
        zmin, ymin, xmin, _, _, _ = obj.bbox
        zz = df_coords.loc[[obj.label], 'z']
        df_coords.loc[[obj.label], 'z_local'] = zz - zmin
        yy = df_coords.loc[[obj.label], 'y']
        df_coords.loc[[obj.label], 'y_local'] = yy - ymin  
        xx = df_coords.loc[[obj.label], 'x']
        df_coords.loc[[obj.label], 'x_local'] = xx - xmin  
    
    return df_coords

def from_spots_coords_to_spots_masks(spots_coords, spot_zyx_size, debug=False):
    spot_mask = get_local_spheroid_mask(spot_zyx_size)
    spots_masks = [
        spot_mask.copy() for _ in range(len(spots_coords))
    ]
    return spots_masks

def from_df_spots_objs_to_spots_lab(df_spots_objs, arr_shape, spots_lab=None):
    if spots_lab is None:
        spots_lab = np.zeros(arr_shape, dtype=np.uint32)
    
    if spots_lab.ndim == 2:
        spots_lab = spots_lab[np.newaxis]
    
    for row in df_spots_objs.itertuples():
        ID, spot_id = row.Index
        spot_mask = row.spot_mask
        zyx_center = (row.z, row.y, row.x)
        slices = get_slices_local_into_global_3D_arr(
            zyx_center, arr_shape, spot_mask.shape
        )
        slice_global_to_local, slice_crop_local = slices
        cropped_spot_mask = spot_mask[slice_crop_local].copy()
        spots_lab[slice_global_to_local][cropped_spot_mask] = spot_id
    return spots_lab

def add_closest_ID_col(df_spots_coords, lab, zyx_coords_cols):
    df_spots_coords['closest_ID'] = df_spots_coords.index.to_list()
    
    if 0 not in df_spots_coords.index:
        return df_spots_coords
    
    zyx_coords = df_spots_coords.loc[[0], zyx_coords_cols].to_numpy()
    closest_IDs = []
    for zc, yc, xc in zyx_coords:
        if lab.ndim == 3:
            lab_2D = lab[zc]
        else:
            lab_2D = lab
        
        closest_ID = cellacdc.core.nearest_nonzero_2D(lab_2D, yc, xc)
        closest_IDs.append(closest_ID)
    df_spots_coords.loc[[0], 'closest_ID'] = closest_IDs
    return df_spots_coords
    
        
    