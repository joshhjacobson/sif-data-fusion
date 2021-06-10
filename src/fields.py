from datetime import datetime
from dateutil.relativedelta import relativedelta

import numpy as np
from numpy.lib.function_base import median
import pandas as pd
import xarray as xr

import krige_tools
from variogram import shift_longitude, empirical_variogram


def get_scale_factor(ds, data_name):
    """Computes the initial univariate semivariograms, and returns the square root of each semivariogram value based on the most pairs around a lag of 1000 km."""
    # Compute the semivariogram
    df = ds[data_name].to_dataframe().reset_index().dropna(subset=[data_name])
    values = df[data_name].values
    coords = df[["lat", "lon"]].values

    if values.size == 0:
        return np.nan

    dist = krige_tools.distance_matrix(coords, shift_longitude(coords), fast_dist=True)
    df_vgm = empirical_variogram(dist, values, n_bins=50, covariogram=False)

    # Get the root of the value which uses the most pairs around lag 1000 km
    return np.sqrt(
        df_vgm[df_vgm["bin_center"].between(900, 1100)]
        .sort_values("count", ascending=False)["bin_mean"]
        .values[0]
    )


def median_abs_dev(x):
    # NOTE: see https://en.wikipedia.org/wiki/Median_absolute_deviation for details
    k = 1.4826  # scale factor assuming a normal distribution
    return k * np.nanmedian(np.abs(x - np.nanmedian(x)))


def preprocess_ds(ds, timestamp):
    """Apply data transformations and compute surface mean and standard deviation."""
    data_name, var_name = krige_tools.get_field_names(ds)

    # TODO: save actual trend so it can be added back to field in prediction
    ds[data_name] = krige_tools.remove_linear_trend(ds[data_name])

    # Select data at timestamp only
    ds_field = ds.sel(time=timestamp)

    # Remove the OLS mean surface
    ds_field["spatial_mean"] = krige_tools.fit_ols(ds_field, data_name)
    ds_field[data_name] = ds_field[data_name] - ds_field["spatial_mean"]

    # Rescale the data
    # ds_field.attrs["scale_fact"] = get_scale_factor(ds_field, data_name)
    # ds_field[data_name] = ds_field[data_name] / ds_field.attrs["scale_fact"]

    # Divide by custom standard dev. calculated from residuals at all spatial locations
    # ds_field.attrs["scale_fact"] = np.nanstd(ds_field[data_name].values)
    ds_field.attrs["scale_fact"] = median_abs_dev(ds_field[data_name].values)
    ds_field[data_name] = ds_field[data_name] / ds_field.attrs["scale_fact"]

    # Remove outliers and return
    return ds_field
    # .where(np.abs(ds_field[data_name]) <= 3)


class Field:
    """
    Stores data values and coordinates for a single process at a fixed time in a data frame.
    """

    def __init__(self, ds, timestamp):
        self.timestamp = timestamp
        self.data_name, self.var_name = krige_tools.get_field_names(ds)
        ds_prep = preprocess_ds(ds.copy(), timestamp)
        df = ds_prep.to_dataframe().reset_index().dropna(subset=[self.data_name])
        self.ds = ds_prep
        self.coords = df[["lat", "lon"]].values
        self.values = df[self.data_name].values
        self.spatial_mean = df["spatial_mean"].values
        self.scale_fact = ds_prep.attrs["scale_fact"]
        self.variance_estimate = df[self.var_name].values

    def to_xarray(self):
        """Converts the field to an xarray dataset."""
        return (
            pd.DataFrame(
                {
                    "lat": self.coords[:, 0],
                    "lon": self.coords[:, 1],
                    "values": self.values,
                }
            )
            .set_index(["lon", "lat"])
            .to_xarray()
        )

    # def get_spatial_df(self):
    #     """Converts the spatial dataset associated with the timestamp to a data frame with location ids."""
    #     # NOTE: assumes data is already mean-zero
    #     df = (
    #         self.ds[self.data_name]
    #         .to_dataframe()
    #         .drop(columns="time")
    #         .dropna()
    #         .reset_index()
    #     )
    #     # Assign location and time IDs
    #     df["loc_id"] = df.groupby(["lat", "lon"]).ngroup()
    #     return df

    # def get_spacetime_df(self):
    #     """Converts the spatio-temporal dataset associated with the timestamp to a data frame."""
    #     # NOTE: assumes data is already mean-zero
    #     df = self.ds[self.data_name].to_dataframe().dropna().reset_index()
    #     # Assign location and time IDs
    #     df["loc_id"] = df.groupby(["lat", "lon"]).ngroup()
    #     df["t_id"] = df.groupby(["time"]).ngroup()
    #     return df


class MultiField:
    """
    Main class.
    """

    def __init__(
        self, ds_1, ds_2, timestamp, timedelta=0, dist_units="km", fast_dist=False,
    ):
        self.timestamp = np.datetime_as_string(timestamp, unit="D")
        self.timedelta = timedelta
        self.dist_units = dist_units
        self.fast_dist = fast_dist
        self.ds_1 = ds_1
        self.ds_2 = ds_2
        self.field_1 = Field(ds_1, timestamp)
        self.field_2 = Field(ds_2, self._apply_timedelta())
        self.joint_data_vec = np.hstack((self.field_1.values, self.field_2.values))
        # self.joint_std_inverse = np.float_power(
        #     np.hstack((self.field_1.temporal_std, self.field_2.temporal_std)), -1
        # )

    def _apply_timedelta(self):
        """Returns timestamp with month offset by timedelta as string."""
        t0 = datetime.strptime(self.timestamp, "%Y-%m-%d")
        return (t0 + relativedelta(months=self.timedelta)).strftime("%Y-%m-%d")

    def get_joint_dists(self):
        """Computes block distance matrices and returns the blocks in a dict."""
        off_diag = krige_tools.distance_matrix(
            self.field_1.coords,
            self.field_2.coords,
            units=self.dist_units,
            fast_dist=self.fast_dist,
        )
        return {
            "block_11": krige_tools.distance_matrix(
                self.field_1.coords,
                self.field_1.coords,
                units=self.dist_units,
                fast_dist=self.fast_dist,
            ),
            "block_12": off_diag,
            "block_21": off_diag.T,
            "block_22": krige_tools.distance_matrix(
                self.field_2.coords,
                self.field_2.coords,
                units=self.dist_units,
                fast_dist=self.fast_dist,
            ),
        }
