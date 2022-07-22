import logging
import numpy as np
import prettytable
from typing import Tuple, Union
from xrprimer.utils.log_utils import get_logger


def __init_valid_views_dict__(concerned_n_view: int = 3) -> dict:
    """Create a dict for valid view number statistics。

    Args:
        concerned_n_view (int, optional):
            If a point's valid view number is no greater than
            concerned_n_view, it will be counted.
            Defaults to 3.

    Returns:
        dict
    """
    valid_views_dict = {}
    for n_view in range(concerned_n_view):
        valid_views_dict[n_view] = 0.0
    return valid_views_dict


def get_valid_views_stats(points_mask: np.ndarray,
                          concerned_n_view: int = 3,
                          return_rate: bool = True) -> Tuple[dict, str]:
    """Ignoring masked keypoints, define a pair containing all views about a
    single keypoint, in one frame. Count how many valid views in each pair, and
    print percentage of critical pairs in a table.

    Args:
        points_mask (np.ndarray):
            An ndarray of mask, in shape
            [n_view, n_point, 1].
        concerned_n_view (int, optional):
            If a point's valid view number is no greater than
            concerned_n_view, it will be counted.
            Defaults to 3.
        return_rate (bool, optional):
            Whether to return invalid rate.
            If false, return invalid_pairs count,
            else return invalid_pairs / total_pairs.
            Defaults to True.
    Returns:
        Tuple[dict, str]:
            valid_stats_dict(dict):
                Keys are view number and
                values are pair number/ratio.
            table(str):
                Table of valid_stats_dict
                generated by prettytable.
    """
    # init valid count
    valid_stats_dict = __init_valid_views_dict__(
        concerned_n_view=concerned_n_view)
    total_pairs = points_mask.shape[1]
    for point_index in range(total_pairs):
        pair_data = points_mask[:, point_index, 0]
        nan_data = pair_data[np.isnan(pair_data)]
        # if marked by nan, skip
        if len(nan_data) > 0:
            total_pairs -= 1
            continue
        valid_mask = (pair_data == 1.0)
        # check how many valid views in one data pair
        n_valid = int(np.sum(valid_mask, axis=0))
        # if critical, count it
        if n_valid in valid_stats_dict.keys():
            valid_stats_dict[n_valid] += 1.0
    # get ratio if required
    if return_rate and total_pairs > 0.0:
        for key in valid_stats_dict.keys():
            valid_stats_dict[key] = \
                valid_stats_dict[key] / float(total_pairs)
    table = prettytable.PrettyTable()
    table.field_names = ['Valid Views', 'Pairs']
    for key, item in valid_stats_dict.items():
        table.add_row([key, item])
    table = '\n' + table.get_string()
    return valid_stats_dict, table


def prepare_triangulate_input(
    camera_number: int,
    points: Union[np.ndarray, list, tuple],
    points_mask: Union[np.ndarray, list, tuple] = None,
    logger: Union[None, str, logging.Logger] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """Prepare points and points_mask to numpy ndarray. If check fails, raise
    an error.

    Args:
        camera_number (int):
            Number of cameras.
        points (Union[np.ndarray, list, tuple]):
                An ndarray or a nested list of points2d, in shape
                [n_view, ..., 2+n], n >= 0.
                [...] could be [n_keypoints],
                [n_frame, n_keypoints],
                [n_frame, n_person, n_keypoints], etc.
                If length of the last dim is greater
                than 2, the redundant data will be
                concatenated to output, not modified.
        points_mask (Union[np.ndarray, list, tuple], optional):
            An ndarray or a nested list of mask, in shape
            [n_view, ..., 1].
            If points_mask[index] == 1, points[index] is valid
            for triangulation, else it is ignored.
            If points_mask[index] == np.nan, the whole pair will
            be ignored and not counted by any method.
            Defaults to None.
        logger (Union[None, str, logging.Logger], optional):
            Logger for logging. If None, root logger will be selected.
            Defaults to None.

    Raises:
        TypeError: Type of points is not in (np.ndarray, list, tuple).
        TypeError: Type of points_mask is not in (np.ndarray, list, tuple).
        ValueError: View number of input does not match camera_number.
        ValueError: points.shape[-1] must not be fewer than 2.
        ValueError: points_mask must be [n_view, ..., 1] like points.

    Returns:
        Tuple[np.ndarray, np.ndarray]:
            points and points_mask in type ndarray.
    """
    logger = get_logger(logger)
    if isinstance(points, list) or isinstance(points, tuple):
        points = np.asarray(points)
    # check points type
    if not isinstance(points, np.ndarray):
        logger.error('Type of points is not in (np.ndarray, list, tuple).\n' +
                     f'Type: {type(points)}.')
        raise TypeError
    if points_mask is None:
        points_mask = np.ones_like(points[..., 0:1])
    elif isinstance(points_mask, list) or isinstance(points_mask, tuple):
        points_mask = np.asarray(points_mask)
    # check points_mask type
    if not isinstance(points_mask, np.ndarray):
        logger.error(
            'Type of points_mask is not in (np.ndarray, list, tuple).\n' +
            f'Type: {type(points_mask)}.')
        raise TypeError
    # check points shape
    if not (points.shape[0] == points_mask.shape[0]
            and points.shape[0] == camera_number):
        logger.error('View number of input does not' +
                     ' equal to triangulator\'s camera number.\n' +
                     f'points.shape: {points.shape}\n' +
                     f'points_mask.shape: {points_mask.shape}\n' +
                     f'camera number: {camera_number}\n')
        raise ValueError
    if points.shape[-1] < 2:
        logger.error('points.shape[-1] must not be fewer than 2.' +
                     f'points.shape: {points.shape}')
        raise ValueError
    # check points_mask shape
    if points.shape[:-1] != points_mask.shape[:-1] or\
            points_mask.shape[-1] != 1:
        logger.error('points_mask must be [n_view, ..., 1] like points.' +
                     f'points_mask.shape: {points_mask.shape}' +
                     f'points.shape: {points.shape}')
        raise ValueError
    return points, points_mask


def parse_keypoints_mask(
    keypoints: Union[np.ndarray, list, tuple],
    keypoints_mask: Union[np.ndarray, list, tuple] = None,
    logger: Union[None, str, logging.Logger] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """Get a new selection mask according to keypoints_mask.

    Args:
        keypoints (Union[np.ndarray, list, tuple]):
            An ndarray or a nested list of points2d, in shape
            [n_view, ..., n_keypoints, 2+n].
            [...] could be [n_keypoints],
            [n_frame, n_keypoints],
            [n_frame, n_person, n_keypoints], etc.
            It offers a shape reference for the returned mask.
        keypoints_mask (Union[np.ndarray, list, tuple], optional):
            keypoints_mask in HumanData,
            marking which keypoints is valid.
            Defaults to None.
        logger (Union[None, str, logging.Logger], optional):
                Logger for logging. If None, root logger will be selected.
                Defaults to None.

    Raises:
        ValueError:
            Keypoints number of points does not
            match length of keypoints_mask.

    Returns:
        np.ndarray:
            triangulate_mask for points selection and
            triangulation.
    """
    logger = get_logger(logger)
    keypoints, triangulate_mask = prepare_triangulate_input(
        camera_number=keypoints.shape[0], points=keypoints, logger=logger)
    init_points_mask_shape = triangulate_mask.shape
    if keypoints.shape[-2] != keypoints_mask.shape[0]:
        logger.error('Keypoints number of points does not' +
                     ' match length of keypoints_mask.\n' +
                     f'keypoints.shape: {keypoints.shape}' +
                     f'keypoints_mask.shape: {keypoints_mask.shape}')
        raise ValueError
    nan_indexes = np.where(keypoints_mask == 0)
    triangulate_mask = triangulate_mask.reshape(-1, init_points_mask_shape[-2],
                                                init_points_mask_shape[-1])
    triangulate_mask[:, nan_indexes[0], :] = np.nan
    triangulate_mask = triangulate_mask.reshape(*init_points_mask_shape)
    return triangulate_mask
