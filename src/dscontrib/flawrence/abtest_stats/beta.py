# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import pandas as pd
import numpy as np
import scipy.stats as st

from dscontrib.flawrence.abtest_stats import (
    res_columns, one_res_index, compare_two_sample_sets
)


def compare_two(
    df, col_label, focus_label=None, control_label='control', num_samples=10000
):
    """Jointly sample conversion rates for two branches then compare them.

    See `compare_two_from_summary` for more details.

    Args:
        df: a pandas DataFrame of experiment data. Each row represents
            data about an individual test subject. One column is named
            'branch' and contains the test subject's branch. The other
            columns contain the test subject's values for each metric.
            The column to be analyzed (named `col_label`) should be
            boolean or 0s and 1s.
        col_label: Label for the df column contaning the metric to be
            analyzed.
        focus_label: String in `df['branch']` that identifies the
            target non-control branch, the branch for which we want to
            calculate uplifts.
        control_label: String in `df['branch']` that identifies the
            control branch, the branch with respect to which we want to
            calculate uplifts.
        num_samples: The number of samples to compute

    Returns a pandas.Series of summary statistics for the possible
    uplifts - see docs for `compare_two_sample_sets`
    """
    # I would have used `isin` but it seems to be ~100x slower?
    assert ((df[col_label] == 0) | (df[col_label] == 1)).all()

    summary = df.groupby('branch')[col_label].agg({
        'num_enrollments': len,
        'num_conversions': np.sum
    })

    if len(summary) > 2:
        # Multi-branch test; need to know which two branches to compare
        assert focus_label is not None
        summary = summary.loc[[focus_label, control_label]]

    return compare_two_from_summary(
        summary, control_label=control_label, num_samples=num_samples
    )


def compare_many(df, col_label, num_samples=10000):
    """Jointly sample conversion rates for many branches then compare them.

    See `compare_many_from_summary` for more details.

    Args:
        df: a pandas DataFrame of experiment data. Each row represents
            data about an individual test subject. One column is named
            'branch' and contains the test subject's branch. The other
            columns contain the test subject's values for each metric.
            The column to be analyzed (named `col_label`) should be
            boolean or 0s and 1s.
        col_label: Label for the df column contaning the metric to be
            analyzed.
        num_samples: The number of samples to compute

    Returns a pandas.DataFrame of summary statistics for the possible
    uplifts:
        - columns: equivalent to rows output by `compare_two()`
        - index: list of branches
    """
    assert (df[col_label] == 0) | (df[col_label] == 1).all()
    summary = df.groupby('branch')[col_label].agg({
        'num_enrollments': len,
        'num_conversions': np.sum
    })

    return compare_many_from_summary(
        summary, num_samples=num_samples
    )


def summarize_one_from_summary(
    s, num_enrollments_label='num_enrollments', num_conversions_label='num_conversions'
):
    res = pd.Series(index=one_res_index)
    res['mean'] = s.loc[num_conversions_label] / s.loc[num_enrollments_label]

    ppfs = [0.005, 0.05, 0.95, 0.995]
    res[[str(v) for v in ppfs]] = st.beta(
        s.loc[num_conversions_label] + 1,
        s.loc[num_enrollments_label] - s.loc[num_conversions_label] + 1
    ).ppf(ppfs)

    return res


def compare_two_from_summary(
    df,
    control_label='control',
    num_enrollments_label='num_enrollments',
    num_conversions_label='num_conversions',
    num_samples=10000
):
    """Jointly sample conversion rates for two branches then compare them.

    Calculates various quantiles on the uplift of the non-control
    branch's sampled conversion rates with respect to the control
    branch's sampled conversion rates.

    The data in `df` is modelled as being generated binomially, with a
    Beta(1, 1) (uniform) prior over the conversion rate parameter.

    Args:
        df: A pandas dataframe of integers:
            - df.index lists the two experiment branches
            - df.columns is
                (num_enrollments_label, num_conversions_label)
        control_label: Label for the df row containing data for the
            control branch
        num_enrollments_label: Label for the df column containing the
            number of enrollments in each branch.
        num_conversions_label: Label for the df column containing the
            number of conversions in each branch.
        num_samples: The number of samples to compute

    Returns a pandas.Series of summary statistics for the possible
    uplifts - see docs for `compare_two_sample_sets`
    FIXME: update docs
    """
    assert len(df.index) == 2
    assert control_label in df.index, "Which branch is the control?"

    test_label = list(set(df.index) - {control_label})[0]

    samples = _generate_samples(
        df, num_enrollments_label, num_conversions_label, num_samples
    )

    comparative = compare_two_sample_sets(samples[test_label], samples[control_label])
    comparative.name = num_conversions_label

    individual = {
        l: summarize_one_from_summary(
            df.loc[l], num_enrollments_label, num_conversions_label
        ) for l in [control_label, test_label]
    }

    return {
        'comparative': comparative,
        'individual': individual
    }


def compare_many_from_summary(
    df,
    num_enrollments_label='num_enrollments',
    num_conversions_label='num_conversions',
    num_samples=10000
):
    """Jointly sample conversion rates for many branches then compare them.

    Calculates various quantiles on the uplift of each branch's sampled
    conversion rates, with respect to the best of the other branches'
    sampled conversion rates.

    The data in `df` is modelled as being generated binomially, with a
    Beta(1, 1) (uniform) prior over the conversion rate parameter.

    Args:
        df: A pandas dataframe of integers:
            - df.index lists the experiment branches
            - df.columns is
                (num_enrollments_label, num_conversions_label)
        control_label: Label for the df row containing data for the
            control branch
        num_enrollments_label: Label for the df column containing the
            number of enrollments in each branch.
        num_conversions_label: Label for the df column containing the
            number of conversions in each branch.
        num_samples: The number of samples to compute

    Returns a pandas.DataFrame of summary statistics for the possible
    uplifts:
        - columns: equivalent to rows output by `compare_two()`
        - index: list of branches
    """
    samples = _generate_samples(
        df, num_enrollments_label, num_conversions_label, num_samples
    )

    comparative = pd.DataFrame(index=df.index, columns=res_columns)
    comparative.name = num_conversions_label

    individual = {}

    for branch in df.index:
        # Compare this branch to the best of the rest
        # (beware Monty's Revenge!)
        this_branch = samples[branch]
        # Warning: assumes we're trying to maximise the metric
        best_of_rest = samples.drop(branch, axis='columns').max(axis='columns')

        comparative.loc[branch] = compare_two_sample_sets(this_branch, best_of_rest)
        individual[branch] = summarize_one_from_summary(
            df.loc[branch], num_enrollments_label, num_conversions_label
        )

    return {
        'comparative': comparative,
        'individual': individual
    }


def _generate_samples(
    df, num_enrollments_label, num_conversions_label, num_samples
):
    """Return samples from Beta distributions.

    Assumes a Beta(1, 1) prior.

        Args:
        df: A pandas dataframe of integers:
            - df.index lists the experiment branches
            - df.columns is
                (num_enrollments_label, num_conversions_label)
        num_enrollments_label: Label for the df column containing the
            number of enrollments in each branch.
        num_conversions_label: Label for the df column containing the
            number of conversions in each branch.
        num_samples: The number of samples to compute

    Returns a pandas.DataFrame of sampled conversion rates:
        - columns: list of branches
        - index: enumeration of samples
    """
    samples = pd.DataFrame(index=np.arange(num_samples), columns=df.index)
    for branch_label, r in df.iterrows():
        # Oh, for a prior...
        samples[branch_label] = np.random.beta(
            r.loc[num_conversions_label] + 1,
            r.loc[num_enrollments_label] - r.loc[num_conversions_label] + 1,
            size=num_samples
        )

    return samples
