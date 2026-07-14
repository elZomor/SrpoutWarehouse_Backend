mm:
	docker exec -it srpoutwarehouse_backend-web-1 python manage.py makemigrations
m:
	docker exec -it srpoutwarehouse_backend-web-1 python manage.py migrate