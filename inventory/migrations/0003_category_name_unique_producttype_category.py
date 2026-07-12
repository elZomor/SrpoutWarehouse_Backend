# Generated manually (WRH-61 AC-4: Category selectable on the Product Type form)

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_category"),
    ]

    operations = [
        migrations.AlterField(
            model_name="category",
            name="name",
            field=models.CharField(db_index=True, max_length=255, unique=True),
        ),
        migrations.AddField(
            model_name="producttype",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="product_types",
                to="inventory.category",
            ),
        ),
    ]
