import warnings

from numba import njit
import numpy as np
import pandas as pd
import xarray as xr

from scipy.spatial.distance import cdist
from geopy.distance import geodesic
from sklearn.metrics.pairwise import haversine_distances
from sklearn.linear_model import LinearRegression

import data_utils
from stat_tools import standardize, simple_linear_regression


def fit_linear_trend(da):
    """Computes the monthly average of all spatial locations, and removes the trend fit by a linear model."""
    x = da.mean(dim=["lat", "lon"])
    trend = simple_linear_regression(x.values)
    return xr.DataArray(trend, dims=["time"], coords={"time": da.time})


def fit_ols(ds, data_name):
    """Fit and predict the mean surface using ordinary least squares with standarized coordinates."""
    df = ds[data_name].to_dataframe().drop(columns=["time"]).dropna().reset_index()
    if df.shape[0] == 0:
        # no data
        return ds[data_name] * np.nan
    else:
        coords = df[["lon", "lat"]].apply(lambda x: standardize(x), axis=0)
        model = LinearRegression().fit(coords, df.iloc[:, -1])
        df = df.iloc[:, :-1]
        df["ols_mean"] = model.predict(coords)
        return (
            df.set_index(["lon", "lat"])
            .to_xarray()
            .assign_coords(coords={"time": ds[data_name].time})["ols_mean"]
        )


def expand_grid(*args):
    """
    Returns an array of all combinations of elements in the supplied vectors.
    """
    return np.array(np.meshgrid(*args)).T.reshape(-1, len(args))


def distance_matrix(X1, X2, units="km", fast_dist=False):
    """
    Computes the geodesic (or great circle if fast_dist=True) distance among all pairs of points given two sets of coordinates.
    Wrapper for scipy.spatial.distance.cdist using geopy.distance.geodesic as a the metric.

    NOTE: 
    - points should be formatted in rows as [lat, lon]
    - if fast_dist=True, units are kilometers regardless of specification
    """
    # enforce 2d array in case of single point
    X1 = np.atleast_2d(X1)
    X2 = np.atleast_2d(X2)
    if fast_dist:
        # great circle distances in kilometers
        EARTH_RADIUS = 6371  # radius in kilometers
        X1_r = np.radians(X1)
        X2_r = np.radians(X2)
        return haversine_distances(X1_r, X2_r) * EARTH_RADIUS
    else:
        # geodesic distances in specified units
        return cdist(X1, X2, lambda s_i, s_j: getattr(geodesic(s_i, s_j), units))


# def match_data_locations(field_1, field_2):
#     """Only keep data at shared locations"""
#     df_1 = pd.DataFrame(
#         {
#             "lat": field_1.coords[:, 0],
#             "lon": field_1.coords[:, 1],
#             "values": field_1.values,
#         }
#     )
#     df_2 = pd.DataFrame(
#         {
#             "lat": field_2.coords[:, 0],
#             "lon": field_2.coords[:, 1],
#             "values": field_2.values,
#         }
#     )
#     df = pd.merge(df_1, df_2, on=["lat", "lon"], suffixes=("_1", "_2"))
#     return df.values_1, df.values_2


# TODO: test whether numba is actually faster here using toy arrays
# @njit
def pre_post_diag(u, A, v=None):
    """Returns the matrix product: diag(u) A diag(v).

    params:
        - v, u: vector(s) passed to np.diag()
        - A: matrix
    """
    if v is None:
        v = u
    return np.matmul(np.diag(u), np.matmul(A, np.diag(v)))
    # return np.diag(u) @ A @ np.diag(v)  # matmul doesn't play with numba
