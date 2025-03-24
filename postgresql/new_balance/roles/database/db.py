version = '15' # TODO захардкожено исправить
postgres_path = f'/etc/postgresql/{version}/contrprimer'
log = '/tmp/contrprimer'

create_new_cluseter = {
    'g_database': {
        'create_folder':{
            'command':f'sudo mkdir {postgres_path} && sudo chown postgres:postgres {postgres_path}',
            'signal set':'',
            'signal get':''
        },
        'init db':{
            'command':f'sudo su - postgres -c "/usr/lib/postgresql/{version}/bin/initdb -D {postgres_path}"',
            'signal set':'',
            'signal get':''
        }
    }
}

base_config_server = {
    'suac': {
        'path': '/etc/postgresql/15/contrprimer/postgresql.conf',
        'old':'# IPv4 local connections:',
        'new':'' 
    },
}

start_cluster = {
    'g_database': {
        'start db':{
            'command':f'sudo su - postgres -c "/usr/lib/postgresql/15/bin/pg_ctl -D /etc/postgresql/15/contrprimer -l {log}',
            'signal set':'',
            'signal get':''
        }
    }
}
