UPDATE planet_osm_line
  SET mz_road_level = mz_calculate_road_level(highway, railway, aeroway, route,
    service, aerialway, leisure, sport, man_made, way)
  WHERE
    railway IN ('subway', 'funicular');

UPDATE planet_osm_line
  SET mz_transit_level = mz_calculate_transit_level(route)
  WHERE
    route = 'railway';
