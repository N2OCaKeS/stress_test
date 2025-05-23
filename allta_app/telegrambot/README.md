# ALLTAbot
Телеграм бот используется для управления некоторыми функциями оркестратора [ALLTA](http://allta.devos.astralinux.ru)   
и информирования о выходе обновлений, завершении прогонов и т.д.       
      
### Основные возможности   
-  /log - получить прогресс выполнения прогона    
-  /status - узнать статус прогона       
-  /addrc - добавить конфигурацию новой версии релиз кандидата     
-  /acs - сделать снимок для выбранного стенда    
-  /update_stp - обновить состав тестового прогона     
-  /add_testrun - создать тестовый прогон    
-  /runtests - запустить тесты на стенде     
-  /runalltests - запустить тесты на всех стендах     
      
     
## Последовательность команд при выпуске нового RC
Выпуски подразделяются на оперативное обновление и срочное оперативное обновление **UU**    
Команда *"/acs"* используется для всех стендов поочередно, с интервалом в 1-2 минуты    

### При первом и последем RC      
**Оперативное обновление:**    
-  /addrc 1.х.х.х <'password'>    
-  /add_testrun 1.х.х.х <'password'> final    
-  /update_stp 1.х.х.х <'password'>     
-  /acs 1.х.х.х <stand#> <'password'>     
      
**Срочное оперативное обновление:**       
-  /addrc 1.х.х.UU.х.x <'password'> 1.х.х.х   
-  /add_testrun 1.х.х.UU.х.x <'password'> notfinal    
-  /update_stp 1.х.х.UU.х.x <'password'>     
-  /acs 1.х.х.UU.х.x <stand#> <'password'> 
     
### При промежуточных RC (прогоны формируются по changelog)
**Оперативное обновление:**    
-  /addrc 1.х.х.х <'password'>    
-  /add_testrun 1.х.х.х <'password'> notfinal    
-  /update_stp 1.х.х.х <'password'>     
-  /acs 1.х.х.х <stand#> <'password'>   
      
**Срочное оперативное обновление:**       
-  /addrc 1.х.х.UU.х.x <'password'> 1.х.х.х   
-  /add_testrun 1.х.х.UU.х.x <'password'> notfinal    
-  /update_stp 1.х.х.UU.х.x <'password'>     
-  /acs 1.х.х.UU.х.x <stand#> <'password'> 
     
### Стенды, которые подлежат обязательному созданию "снимков"    
-  stand3    
-  stand4    
-  stand10    
-  stand11    
-  stand12      
-  stand13     
          
**После того, как новый RC был добавлен, необходимо скопировать файлы в dev ветку ALLTA, сделать push и merge с основной**  
**Перечень файлов:**      
-  allta_conf.json    
-  astra-config.json    
-  box-config.json    
-  ChangeLog    
-  releases-index.json    
-  releases.json    
-  test_times.json    