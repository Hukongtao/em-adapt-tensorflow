import os
import sys
os.environ["CUDA_VISIBLE_DEVICES"] = sys.argv[1]

import tensorflow as tf
import numpy as np
from network import Network
from estep import estep as _estep
from dataset import dataset_tf as dataset
from metrics import metrics_update
import time

class ADAPT(Network):
    def __init__(self,config):
        Network.__init__(self,config)
        self.stride = {}
        self.stride["input"] = 1

        # different lr for different variable
        self.lr_1_list = []
        self.lr_2_list = []

    def build(self):
        if "output" not in self.net:
            with tf.name_scope("placeholder"):
                self.net["input"] = tf.placeholder(tf.float32,[None,self.h,self.w,self.config.get("input_channel",3)])
                self.net["label"] = tf.placeholder(tf.float32,[None,self.h,self.w,1])
                self.net["drop_probe"] = tf.placeholder(tf.float32)

            self.net["output"] = self.create_network()
            self.pred()
        return self.net["output"]

    def create_network(self):
        if "init_model_path" in self.config:
            self.load_init_model()
        with tf.name_scope("vgg") as scope:
            # build block
            block = self.build_block("input",["conv1_1","relu1_1","conv1_2","relu1_2","pool1"])
            block = self.build_block(block,["conv2_1","relu2_1","conv2_2","relu2_2","pool2"])
            block = self.build_block(block,["conv3_1","relu3_1","conv3_2","relu3_2","conv3_3","relu3_3","pool3"])
            block = self.build_block(block,["conv4_1","relu4_1","conv4_2","relu4_2","conv4_3","relu4_3","pool4"])
            block = self.build_block(block,["conv5_1","relu5_1","conv5_2","relu5_2","conv5_3","relu5_3","pool5"])
            fc = self.build_fc(block,["fc6","relu6","drop6","fc7","relu7","drop7","fc8"])

            # classifier
            return tf.nn.softmax(self.net[fc])

    def build_block(self,last_layer,layer_lists):
        for layer in layer_lists:
            if layer.startswith("conv"):
                if layer[4] != "5":
                    with tf.name_scope(layer) as scope:
                        self.stride[layer] = self.stride[last_layer]
                        weights,bias = self.get_weights_and_bias(layer)
                        self.net[layer] = tf.nn.conv2d( self.net[last_layer], weights, strides = [1,1,1,1], padding="SAME", name="conv")
                        self.net[layer] = tf.nn.bias_add( self.net[layer], bias, name="bias")
                        last_layer = layer
                if layer[4] == "5":
                    with tf.name_scope(layer) as scope:
                        self.stride[layer] = self.stride[last_layer]
                        weights,bias = self.get_weights_and_bias(layer)
                        self.net[layer] = tf.nn.atrous_conv2d( self.net[last_layer], weights, rate=2, padding="SAME", name="conv")
                        self.net[layer] = tf.nn.bias_add( self.net[layer], bias, name="bias")
                        last_layer = layer
            if layer.startswith("relu"):
                with tf.name_scope(layer) as scope:
                    self.stride[layer] = self.stride[last_layer]
                    self.net[layer] = tf.nn.relu( self.net[last_layer],name="relu")
                    last_layer = layer
            if layer.startswith("pool"):
                if layer[4] not in ["4","5"]:
                    with tf.name_scope(layer) as scope:
                        self.stride[layer] = 2 * self.stride[last_layer]
                        self.net[layer] = tf.nn.max_pool( self.net[last_layer], ksize=[1,3,3,1], strides=[1,2,2,1],padding="SAME",name="pool")
                        last_layer = layer
                if layer[4] in ["4","5"]:
                    with tf.name_scope(layer) as scope:
                        self.stride[layer] = self.stride[last_layer]
                        self.net[layer] = tf.nn.max_pool( self.net[last_layer], ksize=[1,3,3,1], strides=[1,1,1,1],padding="SAME",name="pool")
                        last_layer = layer
        return last_layer

    def build_fc(self,last_layer, layer_lists):
        for layer in layer_lists:
            if layer.startswith("fc"):
                with tf.name_scope(layer) as scope:
                    weights,bias = self.get_weights_and_bias(layer)
                    if last_layer.startswith("pool"):
                        self.net[layer] = tf.nn.atrous_conv2d( self.net[last_layer], weights, rate=4, padding="SAME", name="conv")

                    else:
                        self.net[layer] = tf.nn.conv2d( self.net[last_layer], weights, strides = [1,1,1,1], padding="SAME", name="conv")
                    last_layer = layer
            if layer.startswith("relu"):
                with tf.name_scope(layer) as scope:
                    self.net[layer] = tf.nn.relu( self.net[last_layer])
                    last_layer = layer
            if layer.startswith("drop"):
                with tf.name_scope(layer) as scope:
                    self.net[layer] = tf.nn.dropout( self.net[last_layer],self.net["drop_probe"])
                    last_layer = layer

        return last_layer

    def pred(self):
        scale_output = tf.image.resize_bilinear(self.net["output"],self.net["input"].shape[1:3])
        self.net["pred"] = tf.argmax(scale_output,axis=3)

    def e_step(self,last_layer, bg_p, fg_p, num_iter, suppress_others, margin_others):
        shrink_label = tf.squeeze(tf.image.resize_nearest_neighbor(self.net["label"],self.net["output"].shape[1:3]),axis=3)
        def estep(feature_map,label):
            s = time.time()
            #print("start time:%f" % s)
            tmp_ = _estep(feature_map,label,suppress_others,num_iter,margin_others,bg_p,fg_p,use_c=True)
            #tmp_ = _estep(feature_map,label,suppress_others,num_iter,margin_others,bg_p,fg_p)
            e = time.time()
            #print("duration time :%f " % (e-s))
            return tmp_
        layer = "e_step"
        self.net[layer] = tf.py_func(estep,[self.net[last_layer],shrink_label],tf.float32)
        last_layer = layer
        layer = "e_argmax"
        self.net[layer] = tf.argmax(self.net[last_layer],axis=3)
        return layer

    def load_init_model(self):
        model_path = self.config["init_model_path"]
        self.init_model = np.load(model_path,encoding="latin1").item()
        print("load init model success: %s" % model_path)

    def get_weights_and_bias(self,layer):
        print("layer: %s" % layer)
        if layer.startswith("conv"):
            shape = [3,3,0,0]
            if layer == "conv1_1":
                shape[2] = 3
            else:
                shape[2] = 64 * self.stride[layer]
                if shape[2] > 512: shape[2] = 512
                if layer in ["conv2_1","conv3_1","conv4_1"]: shape[2] = int(shape[2]/2)
            shape[3] = 64 * self.stride[layer]
            if shape[3] > 512: shape[3] = 512
        if layer.startswith("fc"):
            if layer == "fc6":
                shape = [4,4,512,4096]
            if layer == "fc7":
                shape = [1,1,4096,4096]
            if layer == "fc8": 
                shape = [1,1,4096,self.category_num]
        if "init_model_path" not in self.config:
            init = tf.random_normal_initializer(stddev=0.01)
            weights = tf.get_variable(name="%s_weights" % layer,initializer=init, shape = shape)
            init = tf.constant_initializer(0)
            bias = tf.get_variable(name="%s_bias" % layer,initializer=init, shape = [shape[-1]])
        else:
            if layer == "fc8":
                init = tf.random_normal_initializer(stddev=0.01)
            else:
                init = tf.constant_initializer(self.init_model[layer]["w"])
            weights = tf.get_variable(name="%s_weights" % layer,initializer=init,shape = shape)
            if layer == "fc8":
                init = tf.constant_initializer(0)
            else:
                init = tf.constant_initializer(self.init_model[layer]["b"])
            bias = tf.get_variable(name="%s_bias" % layer,initializer=init,shape = [shape[-1]])
        self.weights[layer] = (weights,bias)
        self.lr_1_list.append(weights)
        self.lr_2_list.append(bias)
        return weights,bias

    def getloss(self):
        weaklabel_layer = self.e_step("fc8", bg_p=0.4, fg_p=0.2, num_iter=5, suppress_others=True, margin_others=1e-5)
        loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(labels=tf.reshape(self.net[weaklabel_layer],[-1]),logits=tf.reshape(self.net["output"],[-1,self.category_num])))
        return loss

    def optimize(self,base_lr,momentum):
        self.net["lr"] = tf.Variable(base_lr, trainable=False)
        opt = tf.train.MomentumOptimizer(self.net["lr"],momentum)
        #self.net["train_op"],self.a = metrics_update(self.loss["total"],opt,kinds=["gradient"])
        update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
        with tf.control_dependencies(update_ops):
            self.net["train_op"] = opt.minimize(self.loss["total"])

    def train(self,base_lr,weight_decay,momentum,batch_size,epoches):
        assert self.data is not None,"data is None"
        assert self.sess is not None,"sess is None"
        self.net["is_training"] = tf.placeholder(tf.bool)
        x_train,y_train,iterator_train = self.data.next_data(category="train",batch_size=batch_size,epoches=-1)
        x_val,y_val,iterator_val = self.data.next_data(category="val",batch_size=batch_size,epoches=-1)
        x = tf.cond(self.net["is_training"],lambda:x_train,lambda:x_val)
        y = tf.cond(self.net["is_training"],lambda:y_train,lambda:y_val)
        self.build()
        self.pre_train(base_lr,weight_decay,momentum,batch_size,save_layers=["input","output","label","pred","is_training","drop_probe"])

        with self.sess.as_default():
            self.sess.run(tf.global_variables_initializer())
            self.sess.run(tf.local_variables_initializer())
            self.sess.run(iterator_train.initializer)
            self.sess.run(iterator_val.initializer)

            if self.config.get("model_path",False) is not False:
                print("start to load model: %s" % self.config.get("model_path"))
                print("before l2 loss:%f" % self.sess.run(self.loss["l2"]))
                self.restore_from_model(self.saver["norm"],self.config.get("model_path"),checkpoint=False)
                print("model loaded ...")
                print("after l2 loss:%f" % self.sess.run(self.loss["l2"]))

            start_time = time.time()
            print("start_time: %f" % start_time)
            print("config -- lr:%f weight_decay:%f momentum:%f batch_size:%f epoches:%f" % (base_lr,weight_decay,momentum,base_lr,epoches))

            epoch,i = 0.0,0
            iterations_per_epoch_train = self.data.get_data_len() // batch_size
            while epoch < epoches:
                if i == 40*iterations_per_epoch_train:
                    new_lr = 0.0003
                    print("save model before new_lr:%f" % new_lr)
                    self.saver["lr"].save(self.sess,os.path.join(self.config.get("saver_path","saver"),"lr-%f" % base_lr),global_step=i)
                    self.sess.run(tf.assign(self.net["lr"],new_lr))

                data_x,data_y = self.sess.run([x,y],feed_dict={self.net["is_training"]:True})
                params = {self.net["input"]:data_x,self.net["label"]:data_y,self.net["drop_probe"]:0.5}
                #l,_,_ = self.sess.run([self.net["lr"],self.a,self.net["train_op"]],feed_dict=params)
                #print("lr: %f" % l)
                #print("x mean:%f, y mean: %f" % (np.mean(data_x),np.mean(data_y)))
                #print("x min:%f, y min: %f" % (np.amin(data_x),np.min(data_y)))
                _ = self.sess.run(self.net["train_op"],feed_dict=params)


                if i%100 in [0,1,2,3,4,5,6,7,8,9]:
                    self.sess.run(self.metrics["update"],feed_dict=params)
                if i%100 == 9:
                    summarys,accu,miou,loss,lr = self.sess.run([self.summary["train"]["op"],self.metrics["accu"],self.metrics["miou"],self.loss["total"],self.net["lr"]],feed_dict=params)
                    self.summary["writer"].add_summary(summarys,i)
                    print("epoch:%f, iteration:%f, lr:%f, loss:%f, accu:%f, miou:%f" % (epoch,i,lr,loss,accu,miou))
                if i%100 == 10:
                    self.sess.run(self.metrics["reset"],feed_dict=params)

                if i%1000 in [10,11,12,13,14,15,16,17,18,19]:
                    data_x,data_y = self.sess.run([x,y],feed_dict={self.net["is_training"]:False})
                    params = {self.net["input"]:data_x,self.net["label"]:data_y,self.net["drop_probe"]:0.5}
                    self.sess.run(self.metrics["update"],feed_dict=params)
                if i%1000 == 19:
                    data_x,data_y = self.sess.run([x,y],feed_dict={self.net["is_training"]:False})
                    params = {self.net["input"]:data_x,self.net["label"]:data_y,self.net["drop_probe"]:0.5}
                    summarys,accu,miou,loss,lr = self.sess.run([self.summary["val"]["op"],self.metrics["accu"],self.metrics["miou"],self.loss["total"],self.net["lr"]],feed_dict=params)
                    self.summary["writer"].add_summary(summarys,i)
                    print("val epoch:%f, iteration:%f, lr:%f, loss:%f, accu:%f, miou:%f" % (epoch,i,lr,loss,accu,miou))
                if i%1000 == 20:
                    data_x,data_y = self.sess.run([x,y],feed_dict={self.net["is_training"]:False})
                    params = {self.net["input"]:data_x,self.net["label"]:data_y,self.net["drop_probe"]:0.5}
                    self.sess.run(self.metrics["reset"],feed_dict=params)

                if i%3000 == 2999:
                    self.saver["norm"].save(self.sess,os.path.join(self.config.get("saver_path","saver"),"norm"),global_step=i)
                i+=1
                epoch = i / iterations_per_epoch_train

            end_time = time.time()
            print("end_time:%f" % end_time)
            print("duration time:%f" %  (end_time-start_time))

if __name__ == "__main__":
    batch_size = 8
    input_size = (321,321)
    category_num = 21
    epoches = 10
    data = dataset({"batch_size":batch_size,"input_size":input_size,"epoches":epoches,"category_num":category_num})
    adapt = ADAPT({"data":data,"batch_size":batch_size,"input_size":input_size,"epoches":epoches,"category_num":category_num,"init_model_path":"./model/init.npy"})
    adapt.train(base_lr=0.001,weight_decay=0,momentum=0.7,batch_size=batch_size,epoches=epoches)