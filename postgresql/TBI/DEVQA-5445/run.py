#####################################
# Created by dtimonin@astralinux.ru #
#####################################


from srv.libs.test import PSQLLoadTest


test = PSQLLoadTest()

"""
TOINFO
Ненужные операции можно отключить переведя соответствующее значение в False
Может пригодиться при повторных запусках, для активации раскомментировать нужное
Пример:
test.host_prepare = False
"""

# test.checking_user = False
# test.checking_mode = False
# test.host_prepare = False
# test.db_prep = False
# test.init_bd = False
# test.execute = False
# test.cleare = True


test.check_user()
test.check_mode()
test.host_env_prepare()
test.database_prep()
test.init_base()
test.execute_test()
test.cleare()


