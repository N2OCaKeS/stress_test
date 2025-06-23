from new_balance import bl_lib
from new_balance.roles.vm_info import VERSION_OS


if VERSION_OS == '1.7':
    bl_lib.balance(rc='1.7.7.6')

elif VERSION_OS == '1.8':
    bl_lib.balance(rc='1.8.3.3')  


