-- sudo -u postgres psql
-- \connect your_database_name
-- \dt
-- SELECT * FROM table_name;



-- CREATE DATABASE bendiks;

-- и запустить скрипт командой: 
-- psql -d bendiks -f create_base.sql

CREATE TABLE main_table (
    id SERIAL PRIMARY KEY,
    stand1_cpu VARCHAR(255),
    stand1_ram VARCHAR(255),
    stand2_cpu VARCHAR(255),
    stand2_ram VARCHAR(255),
    stand3_cpu VARCHAR(255),
    stand3_ram VARCHAR(255),
    stand4_cpu VARCHAR(255),
    stand4_ram VARCHAR(255)
);


-- ALTER TABLE main_table 
-- ADD COLUMN stand10_cpu VARCHAR(255), 
-- ADD COLUMN stand10_ram VARCHAR(255),
-- ADD COLUMN stand11_cpu VARCHAR(255),
-- ADD COLUMN stand11_ram VARCHAR(255),
-- ADD COLUMN stand12_cpu VARCHAR(255),
-- ADD COLUMN stand12_ram VARCHAR(255);


ALTER TABLE main_table 
ADD COLUMN stand1_nvme VARCHAR(255), 
ADD COLUMN stand1_sda VARCHAR(255),
ADD COLUMN stand2_nvme VARCHAR(255), 
ADD COLUMN stand2_sda VARCHAR(255),
ADD COLUMN stand3_nvme VARCHAR(255), 
ADD COLUMN stand3_sda VARCHAR(255),
ADD COLUMN stand4_nvme VARCHAR(255), 
ADD COLUMN stand4_sda VARCHAR(255),
ADD COLUMN stand10_nvme VARCHAR(255), 
ADD COLUMN stand10_sda VARCHAR(255),
ADD COLUMN stand11_nvme VARCHAR(255), 
ADD COLUMN stand11_sda VARCHAR(255),
ADD COLUMN stand12_nvme VARCHAR(255), 
ADD COLUMN stand12_sda VARCHAR(255);


ALTER TABLE main_table 
ADD COLUMN stand1_cpu_user VARCHAR(255), 
ADD COLUMN stand1_cpu_system VARCHAR(255),
ADD COLUMN stand2_cpu_user VARCHAR(255), 
ADD COLUMN stand2_cpu_system VARCHAR(255),
ADD COLUMN stand3_cpu_user VARCHAR(255), 
ADD COLUMN stand3_cpu_system VARCHAR(255),
ADD COLUMN stand4_cpu_user VARCHAR(255), 
ADD COLUMN stand4_cpu_system VARCHAR(255),
ADD COLUMN stand10_cpu_user VARCHAR(255), 
ADD COLUMN stand10_cpu_system VARCHAR(255),
ADD COLUMN stand11_cpu_user VARCHAR(255), 
ADD COLUMN stand11_cpu_system VARCHAR(255),
ADD COLUMN stand12_cpu_user VARCHAR(255), 
ADD COLUMN stand12_cpu_system VARCHAR(255);