import narou_api

r = narou_api.get(st=1, lim=5, order="new")
print(r.text)