SELECT
    gid AS __id__,
    the_geom AS __geometry__,
    featurecla AS kind,
    scalerank::float
FROM
    ne_10m_coastline