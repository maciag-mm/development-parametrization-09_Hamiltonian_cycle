import os
from PyQt5.QtWidgets import QAction, QInputDialog
from PyQt5.QtGui import QIcon, QColor
from PyQt5.QtCore import QVariant
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, 
    QgsField, QgsSpatialIndex, Qgis, QgsCoordinateTransform,
    QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
    QgsMarkerSymbol, QgsPalLayerSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling, QgsApplication
)
from qgis.gui import QgsMapToolEmitPoint

class ParametryzacjaZabudowyPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.nn_click_tool = None
        self.selected_layer = None

    def initGui(self):
        # Wczytanie ikony bezpośrednio z pliku w folderze wtyczki
        icon_path = os.path.join(self.plugin_dir, 'icon.svg')
        self.action = QAction(QIcon(icon_path), "Parametryzacja zabudowy (Hamilton)", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        
        # Dodanie do paska narzędzi i menu "Wtyczki"
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Parametryzacja zabudowy", self.action)

    def unload(self):
        # Sprzątanie po wyłączeniu wtyczki
        self.iface.removePluginMenu("&Parametryzacja zabudowy", self.action)
        self.iface.removeToolBarIcon(self.action)

    def run(self):
        # 1. Filtrowanie warstw wektorowych - punktowych
        point_layers = [
            l for l in QgsProject.instance().mapLayers().values() 
            if isinstance(l, QgsVectorLayer) and l.geometryType() == Qgis.GeometryType.Point
        ]
        
        if not point_layers:
            self.iface.messageBar().pushMessage("Błąd", "Nie znaleziono żadnej wektorowej warstwy punktowej w projekcie.", level=Qgis.Warning)
            return
            
        names = [l.name() for l in point_layers]
        sel_name, ok = QInputDialog.getItem(self.iface.mainWindow(), "Parametryzacja zabudowy", "Wybierz warstwę punktów:", names, 0, False)
        
        if ok and sel_name:
            self.selected_layer = next(l for l in point_layers if l.name() == sel_name)
            canvas = self.iface.mapCanvas()
            
            # Aktywacja narzędzia do klikania
            self.nn_click_tool = QgsMapToolEmitPoint(canvas)
            self.nn_click_tool.canvasClicked.connect(self.on_canvas_clicked)
            canvas.setMapTool(self.nn_click_tool)
            
            self.iface.messageBar().pushMessage("Start", "Kliknij punkt na mapie, od którego ma się rozpocząć trasa.", level=Qgis.Info)

    def on_canvas_clicked(self, point, button):
        canvas = self.iface.mapCanvas()
        canvas.unsetMapTool(self.nn_click_tool) # Wyłączenie narzędzia po kliknięciu
        self.generate_nn_path(self.selected_layer, point)

    def generate_nn_path(self, layer, clicked_point):
        # Transformacja CRS
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        transform = QgsCoordinateTransform(canvas_crs, layer.crs(), QgsProject.instance())
        pt_layer_crs = transform.transform(clicked_point)
        
        # Pobranie punktów
        points = {f.id(): f.geometry().asPoint() for f in layer.getFeatures()}
        if not points:
            self.iface.messageBar().pushMessage("Błąd", "Warstwa jest pusta.", level=Qgis.Warning)
            return

        sp_index = QgsSpatialIndex(layer.getFeatures())
        nearest_ids = sp_index.nearestNeighbor(pt_layer_crs, 1)
        
        if not nearest_ids: return
            
        start_id = nearest_ids[0]
        unvisited = set(points.keys())
        
        # Tworzenie warstwy wynikowej
        out_layer = QgsVectorLayer(f"LineString?crs={layer.crs().authid()}", "Parametryzacja_Trasa", "memory")
        pr = out_layer.dataProvider()
        pr.addAttributes([QgsField("seg_id", QVariant.Int)])
        out_layer.updateFields()
        
        out_features = []
        current_id = start_id
        unvisited.remove(current_id)
        seg_id = 1
        
        self.iface.messageBar().pushMessage("Obliczenia", "Generowanie trasy... proszę czekać.", level=Qgis.Info)
        
        # Algorytm NN
        while unvisited:
            current_pt = points[current_id]
            min_dist = float('inf')
            next_id = None
            
            for uid in unvisited:
                dist = current_pt.sqrDist(points[uid])
                if dist < min_dist:
                    min_dist = dist
                    next_id = uid
            
            if next_id is None: break
            
            feat = QgsFeature(out_layer.fields())
            feat.setGeometry(QgsGeometry.fromPolylineXY([current_pt, points[next_id]]))
            feat.setAttribute("seg_id", seg_id)
            out_features.append(feat)
            
            current_id = next_id
            unvisited.remove(current_id)
            seg_id += 1
            
            # Odmrażanie GUI co 500 iteracji
            if seg_id % 500 == 0:
                QgsApplication.processEvents()

        pr.addFeatures(out_features)
        
        # Stylizacja
        symbol = QgsLineSymbol.createSimple({'line_color': '30,136,229,255', 'line_width': '0.7'})
        marker_layer = QgsMarkerLineSymbolLayer()
        marker_layer.setPlacement(QgsMarkerLineSymbolLayer.LastVertex)
        
        arrow = QgsSimpleMarkerSymbolLayer()
        arrow.setShape(Qgis.MarkerShape.ArrowHead)
        arrow.setColor(QColor(30, 136, 229))
        arrow.setSize(3.5)
        
        m_sym = QgsMarkerSymbol()
        m_sym.changeSymbolLayer(0, arrow)
        marker_layer.setSubSymbol(m_sym)
        symbol.appendSymbolLayer(marker_layer)
        out_layer.renderer().setSymbol(symbol)
        
        # Etykiety
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = "seg_id"
        label_settings.placement = Qgis.LabelPlacement.Line
        label_format = QgsTextFormat()
        label_format.setSize(8)
        label_settings.setFormat(label_format)
        out_layer.setLabelsEnabled(True)
        out_layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
        
        QgsProject.instance().addMapLayer(out_layer)
        self.iface.messageBar().pushMessage("Gotowe", f"Zakończono. Wygenerowano {seg_id-1} odcinków.", level=Qgis.Success)