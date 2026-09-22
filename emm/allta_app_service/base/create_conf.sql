-- sudo -u postgres psql
-- \connect your_database_name
-- \dt
-- SELECT * FROM table_name;



-- CREATE DATABASE b_config;

CREATE TABLE main_table (
    id SERIAL PRIMARY KEY,
    release_version VARCHAR(255),
    releases VARCHAR(255)
);


